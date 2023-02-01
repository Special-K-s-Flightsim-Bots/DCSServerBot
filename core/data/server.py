from __future__ import annotations
import asyncio
import discord
import json
import luadata
import os
import socket
import subprocess
import psycopg2
import uuid
import win32con
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from psutil import Process
from typing import Optional, Union, TYPE_CHECKING
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from .dataobject import DataObject, DataObjectFactory
from .const import Status, Coalition, Channel
from core import utils

if TYPE_CHECKING:
    from core import Plugin, Player, Mission, Extension


class MissionFileSystemEventHandler(FileSystemEventHandler):
    def __init__(self, server: Server):
        self.server = server
        self.bot = server.bot
        self.log = server.log

    def on_created(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if path.endswith('.miz'):
            self.server.addMission(path)
            self.log.info(f"=> New mission {os.path.basename(path)[:-4]} added to server {self.server.name}.")

    def on_deleted(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if path.endswith('.miz'):
            for idx, mission in enumerate(self.server.settings['missionList']):
                if mission == path:
                    if (idx + 1) == self.server.mission_id:
                        self.log.fatal(f'The running mission on server {self.server.name} got deleted!')
                        return
                    self.server.deleteMission(idx + 1)
                    self.log.info(f"=> Mission {os.path.basename(mission)[:-4]} deleted from server {self.server.name}.")


class SettingsDict(dict):
    def __init__(self, server: Server, path: str):
        super().__init__()
        self.path = path
        self.mtime = 0
        self.server = server
        self.bot = server.bot
        self.log = server.log
        self.read_settings()

    def read_settings(self):
        self.mtime = os.path.getmtime(self.path)
        try:
            settings = luadata.read(self.path, encoding='utf-8')
        except Exception as ex:
            # TODO: DSMC workaround
            self.log.debug(f"Exception while reading {self.path}:\n{ex}")
            settings = utils.dsmc_parse_settings(self.path)
            if not settings:
                self.log.error("- Error while parsing serverSettings.lua!")
                raise ex
        if settings:
            self.clear()
            self.update(settings)

    def __setitem__(self, key, value):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_settings()
        super().__setitem__(key, value)
        if len(self):
            with open(self.path, 'wb') as outfile:
                self.mtime = os.path.getmtime(self.path)
                outfile.write(("cfg = " + luadata.serialize(self, indent='\t', indent_level=0)).encode('utf-8'))
        else:
            self.log.error("- Writing of serverSettings.lua aborted due to empty set.")

    def __getitem__(self, item):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_settings()
        return super().__getitem__(item)


@dataclass
@DataObjectFactory.register("Server")
class Server(DataObject):
    name: str = field(compare=False)
    installation: str
    host: str
    port: int
    _channels: dict[Channel, discord.TextChannel] = field(default_factory=dict, compare=False)
    embeds: dict[str, Union[int, discord.Message]] = field(repr=False, default_factory=dict, compare=False)
    _status: Status = field(default=Status.UNREGISTERED, compare=False)
    status_change: asyncio.Event = field(compare=False, init=False)
    options: dict = field(default_factory=dict, compare=False)
    _settings: Optional[SettingsDict] = field(default=None, compare=False)
    current_mission: Mission = field(default=None, compare=False)
    mission_id: int = field(default=-1, compare=False)
    players: dict[int, Player] = field(default_factory=dict, compare=False)
    process: Optional[Process] = field(default=None, compare=False)
    maintenance: bool = field(default=False, compare=False)
    restart_pending: bool = field(default=False, compare=False)
    on_mission_end: dict = field(default_factory=dict, compare=False)
    on_empty: dict = field(default_factory=dict, compare=False)
    dcs_version: str = field(default=None, compare=False)
    extensions: dict[str, Extension] = field(default_factory=dict, compare=False)
    _lock: asyncio.Lock = field(init=False, compare=False)
    afk: dict[str, datetime] = field(default_factory=dict, compare=False)

    def __post_init__(self):
        super().__post_init__()
        self._lock = asyncio.Lock()
        self.status_change = asyncio.Event()
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # read persisted messages for this server
                cursor.execute('SELECT embed_name, embed FROM message_persistence WHERE server_name = %s',
                               (self.name, ))
                for row in cursor.fetchall():
                    self.embeds[row[0]] = row[1]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        # enable autoscan for missions changes
        if self.bot.config.getboolean('BOT', 'AUTOSCAN'):
            self.event_handler = MissionFileSystemEventHandler(self)
            self.observer = Observer()
            self.observer.start()

    @property
    def status(self) -> Status:
        return self._status

    @property
    def missions_dir(self) -> str:
        if 'MISSIONS_DIR' in self.bot.config[self.installation]:
            return os.path.expandvars(self.bot.config[self.installation]['MISSIONS_DIR'])
        else:
            return os.path.expandvars(self.bot.config[self.installation]['DCS_HOME']) + os.path.sep + 'Missions'

    @status.setter
    def status(self, status: Status):
        if status != self._status:
            if self.bot.config.getboolean('BOT', 'AUTOSCAN'):
                if self._status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTDOWN] \
                        and status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
                    if not self.observer.emitters:
                        self.observer.schedule(self.event_handler, self.missions_dir, recursive=False)
                        self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder enabled.')
                elif status == Status.SHUTDOWN:
                    if self._status == Status.UNREGISTERED:
                        # make sure all missions in the directory are in the mission list ...
                        directory = Path(self.missions_dir)
                        missions = self.settings['missionList']
                        i: int = 0
                        for file in directory.glob('*.miz'):
                            if str(file) not in missions:
                                missions.append(str(file))
                                i += 1
                        # make sure the list is written to serverSettings.lua
                        self.settings['missionList'] = missions
                        if i:
                            self.log.info(f"  => {self.name}: {i} missions auto-added to the mission list")
                        if len(missions) > 25:
                            self.log.warning(f"  => {self.name}: You have more than 25 missions registered!"
                                             f" You won't see them all in {self.bot.config['BOT']['COMMAND_PREFIX']}load!")
                    elif self.observer.emitters:
                        self.observer.unschedule_all()
                        self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder disabled.')
            self._status = status
            self.status_change.set()
            self.status_change.clear()

    def add_player(self, player: Player):
        self.players[player.id] = player

    def get_player(self, **kwargs) -> Optional[Player]:
        if 'id' in kwargs:
            if kwargs['id'] in self.players:
                return self.players[kwargs['id']]
            else:
                return None
        for player in self.players.values():
            if player.id == 1:
                continue
            if 'active' in kwargs and player.active != kwargs['active']:
                continue
            if 'ucid' in kwargs and player.ucid == kwargs['ucid']:
                return player
            if 'name' in kwargs and player.name == kwargs['name']:
                return player
            if 'discord_id' in kwargs and player.member and player.member.id == kwargs['discord_id']:
                return player
        return None

    def get_active_players(self) -> list[Player]:
        retval = []
        for player in self.players.values():
            if player.active:
                retval.append(player)
        return retval

    def get_crew_members(self, pilot: Player):
        members = []
        if pilot:
            # now find players that have the same slot
            for player in self.players.values():
                if player.active and player.slot == pilot.slot:
                    members.append(player)
        return members

    def is_populated(self) -> bool:
        if self.status != Status.RUNNING:
            return False
        for player in self.players.values():
            if player.active:
                return True
        return False

    def move_to_spectators(self, player: Player):
        self.sendtoDCS({
            "command": "force_player_slot",
            "playerID": player.id,
            "sideID": 0,
            "slotID": ""
        })

    def kick(self, player: Player, reason: str = 'n/a'):
        self.sendtoDCS({
            "command": "kick",
            "id": player.id,
            "reason": reason
        })

    @property
    def settings(self) -> dict:
        if not self._settings:
            path = os.path.expandvars(self.bot.config[self.installation]['DCS_HOME']) + r'\Config\serverSettings.lua'
            self._settings = SettingsDict(self, path)
        return self._settings

    def get_current_mission_file(self) -> Optional[str]:
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

    async def sendtoDCSSync(self, message: dict, timeout: Optional[int] = 5.0):
        future = self.bot.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.bot.listeners[token] = future
        try:
            self.sendtoDCS(message)
            return await asyncio.wait_for(future, timeout)
        finally:
            del self.bot.listeners[token]

    def sendChatMessage(self, coalition: Coalition, message: str, sender: str = None):
        if coalition == Coalition.ALL:
            self.sendtoDCS({
                "command": "sendChatMessage",
                "from": sender,
                "message": message
            })
        else:
            raise NotImplemented()

    def sendPopupMessage(self, coalition: Coalition, message: str, timeout: Optional[int] = -1, sender: str = None):
        if timeout == -1:
            timeout = self.bot.config['BOT']['MESSAGE_TIMEOUT']
        self.sendtoDCS({
            "command": "sendPopupMessage",
            "to": coalition.value,
            "from": sender,
            "message": message,
            "time": timeout
        })

    def rename(self, new_name: str, update_settings: bool = False) -> None:
        # call rename() in all Plugins
        for plugin in self.bot.cogs.values():  # type: Plugin
            plugin.rename(self.name, new_name)
        # rename the entries in the main database tables
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                               (new_name, self.name))
                cursor.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                               (new_name, self.name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        if update_settings:
            self.settings['name'] = new_name
        self.name = new_name

    async def startup(self) -> None:
        basepath = os.path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION'])
        for exe in ['DCS_server.exe', 'DCS.exe']:
            path = basepath + f'\\bin\\{exe}'
            if os.path.exists(path):
                break
        else:
            self.log.error(f"No executable found to start a DCS server in {basepath}!")
            return
        self.log.debug(r'Launching DCS server with: "{}" --server --norender -w {}'.format(path, self.installation))
        if self.bot.config.getboolean(self.installation, 'START_MINIMIZED'):
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
        timeout = 300 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 180
        self.status = Status.LOADING
        await self.wait_for_status_change([Status.STOPPED, Status.PAUSED, Status.RUNNING], timeout)

    async def shutdown(self) -> None:
        slow_system = self.bot.config.getboolean('BOT', 'SLOW_SYSTEM')
        timeout = 300 if slow_system else 180
        self.sendtoDCS({"command": "shutdown"})
        with suppress(asyncio.TimeoutError):
            await self.wait_for_status_change([Status.STOPPED], timeout)
        if self.process and self.process.is_running():
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
        # make sure, Windows did all cleanups
        if slow_system:
            await asyncio.sleep(10)
        if self.status != Status.SHUTDOWN:
            self.status = Status.SHUTDOWN
        self.process = None

    async def stop(self) -> None:
        if self.status in [Status.PAUSED, Status.RUNNING]:
            timeout = 120 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 60
            self.sendtoDCS({"command": "stop_server"})
            await self.wait_for_status_change([Status.STOPPED], timeout)

    async def start(self) -> None:
        if self.status == Status.STOPPED:
            timeout = 300 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 120
            self.sendtoDCS({"command": "start_server"})
            await self.wait_for_status_change([Status.PAUSED, Status.RUNNING], timeout)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _load(self, message):
        stopped = self.status == Status.STOPPED
        self.sendtoDCS(message)
        self._settings = None
        if not stopped:
            # wait for a status change (STOPPED or LOADING)
            await self.wait_for_status_change([Status.STOPPED, Status.LOADING], timeout=120)
        else:
            self.sendtoDCS({"command": "start_server"})
        # wait until we are running again
        try:
            await self.wait_for_status_change([Status.RUNNING, Status.PAUSED], timeout=300)
        except asyncio.TimeoutError:
            self.log.debug(f'Trying to force start server "{self.name}" due to DCS bug.')
            await self.start()

    def addMission(self, path: str) -> None:
        path = os.path.normpath(path)
        if path in self.settings['missionList']:
            return
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            self.sendtoDCS({"command": "addMission", "path": path})
            self._settings = None
        else:
            missions = self.settings['missionList']
            missions.append(path)
            self.settings['missionList'] = missions

    def deleteMission(self, mission_id: int) -> None:
        if self.status in [Status.PAUSED, Status.RUNNING] and self.mission_id == mission_id:
            raise AttributeError("Can't delete the running mission!")
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            self.sendtoDCS({"command": "deleteMission", "id": mission_id})
            self._settings = None
        else:
            missions = self.settings['missionList']
            del missions[mission_id - 1]
            self.settings['missionList'] = missions

    async def loadMission(self, mission_id: int) -> None:
        await self._load({"command": "startMission", "id": mission_id})

    async def loadNextMission(self) -> None:
        await self._load({"command": "startNextMission"})

    async def setEmbed(self, embed_name: str, embed: discord.Embed, file: Optional[discord.File] = None,
                       channel_id: Optional[Union[Channel, int]] = Channel.STATUS) -> None:
        async with self._lock:
            message = None
            channel = self.bot.get_channel(channel_id) if isinstance(channel_id, int) else self.get_channel(channel_id)
            if embed_name in self.embeds:
                if isinstance(self.embeds[embed_name],  discord.Message):
                    message = self.embeds[embed_name]
                else:
                    try:
                        message = await channel.fetch_message(self.embeds[embed_name])
                        self.embeds[embed_name] = message
                    except discord.errors.NotFound:
                        message = None
                    except discord.errors.DiscordException as ex:
                        self.log.warning(f"Discord error during setEmbed({embed_name}): " + str(ex))
                        return
            if message:
                try:
                    await message.edit(embed=embed)
                except discord.errors.NotFound:
                    message = None
                except discord.errors.DiscordException as ex:
                    self.log.warning(f"Discord error during update of embed {embed_name}: " + str(ex))
                    return
            if not message:
                message = await channel.send(embed=embed, file=file)
                self.embeds[embed_name] = message
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO message_persistence (server_name, embed_name, embed) VALUES (%s, '
                                       '%s, %s) ON CONFLICT (server_name, embed_name) DO UPDATE SET '
                                       'embed=excluded.embed', (self.name, embed_name, message.id))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)

    def get_channel(self, channel: Channel) -> discord.TextChannel:
        if channel not in self._channels:
            self._channels[channel] = self.bot.get_channel(int(self.bot.config[self.installation][channel.value]))
        return self._channels[channel]

    async def wait_for_status_change(self, status: list[Status], timeout: int = 60) -> None:
        async def wait(s: list[Status]):
            while self.status not in s:
                await self.status_change.wait()

        if self.status not in status:
            await asyncio.wait_for(wait(status), timeout)
