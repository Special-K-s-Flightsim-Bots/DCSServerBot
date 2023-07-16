from __future__ import annotations
import asyncio
import os
import uuid
import yaml

from contextlib import suppress
from enum import Enum
from pathlib import Path
from core import utils
from dataclasses import dataclass, field
from datetime import datetime
from psutil import Process
from typing import Optional, Union, TYPE_CHECKING

from .dataobject import DataObject
from .const import Status, Coalition, Channel, Side
from ..plugin import DEFAULT_TAG
from ..services.registry import ServiceRegistry

if TYPE_CHECKING:
    from .player import Player
    from .mission import Mission
    from .instance import Instance
    from ..extension import Extension
    from services import ServiceBus


class UploadStatus(Enum):
    OK = 0
    FILE_EXISTS = 1
    FILE_IN_USE = 2
    READ_ERROR = 3


@dataclass
class Server(DataObject):
    name: str
    port: int
    _channels: dict[Channel, int] = field(default_factory=dict, compare=False)
    _status: Status = field(default=Status.UNREGISTERED, compare=False)
    status_change: asyncio.Event = field(compare=False, init=False)
    _options: Union[utils.SettingsDict, utils.RemoteSettingsDict] = field(default=None, compare=False)
    _settings: Union[utils.SettingsDict, utils.RemoteSettingsDict] = field(default=None, compare=False)
    current_mission: Mission = field(default=None, compare=False)
    mission_id: int = field(default=-1, compare=False)
    players: dict[int, Player] = field(default_factory=dict, compare=False)
    process: Optional[Process] = field(default=None, compare=False)
    _maintenance: bool = field(default=False, compare=False)
    restart_pending: bool = field(default=False, compare=False)
    on_mission_end: dict = field(default_factory=dict, compare=False)
    on_empty: dict = field(default_factory=dict, compare=False)
    dcs_version: str = field(default=None, compare=False)
    extensions: dict[str, Extension] = field(default_factory=dict, compare=False)
    afk: dict[str, datetime] = field(default_factory=dict, compare=False)
    listeners: dict[str, asyncio.Future] = field(default_factory=dict, compare=False)
    locals: dict = field(default_factory=dict, compare=False)
    bus: ServiceBus = field(compare=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.bus = ServiceRegistry.get("ServiceBus")
        self.status_change = asyncio.Event()
        if os.path.exists('config/servers.yaml'):
            data = yaml.safe_load(Path('config/servers.yaml').read_text(encoding='utf-8'))
            if not data.get(self.name):
                self.log.warning(f'No configuration found for server {self.name} in server.yaml!')
            self.locals = data.get(DEFAULT_TAG, {}) | data.get(self.name, {})

    @property
    def is_remote(self) -> bool:
        raise NotImplemented()

    @property
    def instance(self) -> Instance:
        raise NotImplemented()

    @instance.setter
    def instance(self, instance: Instance):
        raise NotImplemented()

    @property
    def status(self) -> Status:
        return self._status

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, maintenance: bool):
        self._maintenance = maintenance

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    @status.setter
    def status(self, status: Union[Status, str]):
        propagate: bool = True
        if isinstance(status, str):
            status = Status(status)
            propagate = False
        if status != self._status:
            self.log.info(f"{self.name}: {self._status.name} => {status.name}")
            self._status = status
            self.status_change.set()
            self.status_change.clear()
            if self.is_remote or (propagate and not self.node.master):
                self.bus.send_to_node({
                    "command": "rpc",
                    "object": "Server",
                    "params": {
                        "status": self.status.value
                    },
                    "server_name": self.name
                }, node=self.node.name)

    @property
    def coalitions(self) -> bool:
        return self.locals.get('coalitions', None) is not None

    async def get_missions_dir(self) -> str:
        pass

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
            if player.active and player.side != Side.SPECTATOR:
                return True
        return False

    def is_public(self) -> bool:
        if self.settings.get('password'):
            return False
        else:
            return True

    def move_to_spectators(self, player: Player, reason: str = 'n/a'):
        self.send_to_dcs({
            "command": "force_player_slot",
            "playerID": player.id,
            "sideID": 0,
            "slotID": "",
            "reason": reason
        })

    def kick(self, player: Player, reason: str = 'n/a'):
        self.send_to_dcs({
            "command": "kick",
            "id": player.id,
            "reason": reason
        })

    def ban(self, ucid: str, reason: str = 'n/a', period: int = 30*86400):
        pass

    def unban(self, ucid: str):
        pass

    async def bans(self) -> list[str]:
        pass

    async def is_banned(self, ucid: str) -> bool:
        pass

    @property
    def settings(self) -> dict:
        raise NotImplemented()

    @property
    def options(self) -> dict:
        raise NotImplemented()

    async def get_current_mission_file(self) -> Optional[str]:
        raise NotImplemented()

    def send_to_dcs(self, message: dict):
        raise NotImplemented()

    def rename(self, new_name: str, update_settings: bool = False) -> None:
        raise NotImplemented()

    async def startup(self) -> None:
        raise NotImplemented()

    async def send_to_dcs_sync(self, message: dict, timeout: Optional[int] = 5.0):
        future = self.bus.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.listeners[token] = future
        try:
            self.send_to_dcs(message)
            return await asyncio.wait_for(future, timeout)
        finally:
            del self.listeners[token]

    def sendChatMessage(self, coalition: Coalition, message: str, sender: str = None):
        if coalition == Coalition.ALL:
            self.send_to_dcs({
                "command": "sendChatMessage",
                "from": sender,
                "message": message
            })
        else:
            raise NotImplemented()

    def sendPopupMessage(self, coalition: Coalition, message: str, timeout: Optional[int] = -1, sender: str = None):
        if timeout == -1:
            timeout = self.locals.get('message_timeout', 10)
        self.send_to_dcs({
            "command": "sendPopupMessage",
            "to": coalition.value,
            "from": sender,
            "message": message,
            "time": timeout
        })

    def playSound(self, coalition: Coalition, sound: str):
        self.send_to_dcs({
            "command": "playSound",
            "to": coalition.value,
            "sound": sound
        })

    async def stop(self) -> None:
        if self.status in [Status.PAUSED, Status.RUNNING]:
            timeout = 120 if self.node.locals.get('slow_system', False) else 60
            self.send_to_dcs({"command": "stop_server"})
            await self.wait_for_status_change([Status.STOPPED], timeout)

    async def start(self) -> None:
        if self.status == Status.STOPPED:
            timeout = 300 if self.node.locals.get('slow_system', False) else 120
            self.status = Status.LOADING
            self.send_to_dcs({"command": "start_server"})
            await self.wait_for_status_change([Status.PAUSED, Status.RUNNING], timeout)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _load(self, message):
        stopped = self.status == Status.STOPPED
        self.send_to_dcs(message)
        if not stopped:
            # wait for a status change (STOPPED or LOADING)
            await self.wait_for_status_change([Status.STOPPED, Status.LOADING], timeout=120)
        else:
            self.send_to_dcs({"command": "start_server"})
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
            self.send_to_dcs({"command": "addMission", "path": path})
        else:
            missions = self.settings['missionList']
            missions.append(path)
            self.settings['missionList'] = missions

    def deleteMission(self, mission_id: int) -> None:
        if self.status in [Status.PAUSED, Status.RUNNING] and self.mission_id == mission_id:
            raise AttributeError("Can't delete the running mission!")
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            self.send_to_dcs({"command": "deleteMission", "id": mission_id})
        else:
            missions = self.settings['missionList']
            del missions[mission_id - 1]
            self.settings['missionList'] = missions

    async def loadMission(self, mission_id: int) -> None:
        await self._load({"command": "startMission", "id": mission_id})

    async def loadNextMission(self) -> None:
        await self._load({"command": "startNextMission"})

    async def modifyMission(self, preset: Union[list, dict]) -> None:
        pass

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        pass

    @property
    def channels(self) -> dict:
        if not self._channels:
            self._channels = {}
            for key, value in self.locals['channels'].items():
                self._channels[Channel(key)] = value
            if Channel.EVENTS not in self._channels:
                self._channels[Channel.EVENTS] = self._channels[Channel.CHAT]
            if Channel.COALITION_BLUE_EVENTS not in self._channels and Channel.COALITION_BLUE_CHAT in self._channels:
                self._channels[Channel.COALITION_BLUE_EVENTS] = self._channels[Channel.COALITION_BLUE_CHAT]
            if Channel.COALITION_RED_EVENTS not in self._channels and Channel.COALITION_RED_CHAT in self._channels:
                self._channels[Channel.COALITION_RED_EVENTS] = self._channels[Channel.COALITION_RED_CHAT]
        return self._channels

    async def wait_for_status_change(self, status: list[Status], timeout: int = 60) -> None:
        async def wait(s: list[Status]):
            while self.status not in s:
                await self.status_change.wait()

        if self.status not in status:
            await asyncio.wait_for(wait(status), timeout)

    async def shutdown(self, force: bool = False) -> None:
        slow_system = self.node.locals.get('slow_system', False)
        timeout = 300 if slow_system else 180
        self.send_to_dcs({"command": "shutdown"})
        with suppress(asyncio.TimeoutError):
            await self.wait_for_status_change([Status.STOPPED], timeout)

    async def init_extensions(self):
        pass
