from __future__ import annotations
import asyncio
import os
import uuid

from core import utils
from core.const import DEFAULT_TAG
from core.utils.performance import PerformanceLog, performance_log
from core.translations import get_translation
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from psutil import Process
from typing import Optional, Union, TYPE_CHECKING, Any

from .dataobject import DataObject
from .const import Status, Coalition, Channel, Side
from ..utils.helper import YAMLError, async_cache

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import Extension, Instance, Mission, UploadStatus, Player
    from services.servicebus import ServiceBus

__all__ = ["Server"]

# Internationalisation
_ = get_translation('core')


@dataclass
class Server(DataObject):
    port: int
    bus: ServiceBus = field(compare=False)
    _instance: Instance = field(compare=False, default=None)
    _channels: dict[Channel, int] = field(default_factory=dict, compare=False)
    _status: Status = field(default=Status.UNREGISTERED, compare=False)
    status_change: asyncio.Event = field(compare=False, init=False)
    _options: Optional[Union[utils.SettingsDict, utils.RemoteSettingsDict]] = field(default=None, compare=False)
    _settings: Optional[Union[utils.SettingsDict, utils.RemoteSettingsDict]] = field(default=None, compare=False)
    current_mission: Optional[Mission] = field(default=None, compare=False)
    mission_id: int = field(default=-1, compare=False)
    players: dict[int, Player] = field(default_factory=dict, compare=False)
    process: Optional[Process] = field(default=None, compare=False)
    _maintenance: bool = field(compare=False, default=False)
    restart_pending: bool = field(default=False, compare=False)
    on_mission_end: dict = field(default_factory=dict, compare=False)
    on_empty: dict = field(default_factory=dict, compare=False)
    extensions: dict[str, Extension] = field(default_factory=dict, compare=False)
    afk: dict[str, datetime] = field(default_factory=dict, compare=False)
    listeners: dict[str, asyncio.Future] = field(default_factory=dict, compare=False)
    locals: dict = field(default_factory=dict, compare=False)
    last_seen: datetime = field(compare=False, default=datetime.now(timezone.utc))
    restart_time: datetime = field(compare=False, default=None)
    idle_since: Optional[datetime] = field(compare=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        self.status_change = asyncio.Event()
        self.locals = self.read_locals()

    async def reload(self):
        raise NotImplementedError()

    def read_locals(self) -> dict:
        config_file = os.path.join(self.node.config_dir, 'servers.yaml')
        if os.path.exists(config_file):
            try:
                validation = self.node.config.get('validation', 'lazy')
                if validation in ['strict', 'lazy']:
                    utils.validate(config_file, ['schemas/servers_schema.yaml'],
                                   raise_exception=(validation == 'strict'))

                data = yaml.load(Path(config_file).read_text(encoding='utf-8'))
            except MarkedYAMLError as ex:
                raise YAMLError(config_file, ex)
            if data.get(self.name) is None and self.name != 'n/a':
                self.log.warning(f'No configuration found for server "{self.name}" in servers.yaml!')
            _locals = utils.deep_merge(data.get(DEFAULT_TAG, {}), data.get(self.name, {}))
            _locals['messages'] = {
                "greeting_message_members": "{player.name}, welcome back to {server.name}!",
                "greeting_message_unmatched": "{player.name}, please use /linkme in our Discord, if you want to see your user stats!",
                "message_server_locked": "This server is currently locked and cannot be joined.",
                "message_player_default_username": "Please change your default player name at the top right of the multiplayer selection list to an individual one!",
                "message_player_username": "Your player name contains invalid characters. Please change your name to join our server.",
                "message_player_inappropriate_username": "Your username contains a curseword. It needs to be changed to join this server.",
                "message_ban": "You are banned from this server. Reason: {}",
                "message_reserved": "This server is locked for specific users.\nPlease contact a server admin.",
                "message_no_voice": 'You need to be in voice channel "{}" to use this server!',
                "message_seat_locked": 'Your player is currently locked.'
            } | _locals.get('messages', {})
            return _locals
        return {}

    @property
    def instance(self) -> Instance:
        return self._instance

    @instance.setter
    def instance(self, instance: Instance):
        self.set_instance(instance)

    def set_instance(self, instance: Instance):
        self._instance = instance
        self._instance.server = self

    @property
    def status(self) -> Status:
        return self._status

    @status.setter
    def status(self, status: Union[Status, str]):
        self.set_status(status)

    # allow overloading of setter
    def set_status(self, status: Union[Status, str]):
        if isinstance(status, str):
            new_status = Status(status)
        else:
            new_status = status
        if new_status != self._status:
            self.log.debug(f"{self.name}: {self._status.name} => {new_status.name}")
            self.last_seen = datetime.now(timezone.utc)
            self._status = new_status
            self.status_change.set()
            self.status_change.clear()
            if not isinstance(status, str) and not (self.node.master and not self.is_remote):
                self.bus.loop.create_task(self.bus.send_to_node({
                    "command": "rpc",
                    "object": "Server",
                    "server_name": self.name,
                    "params": {
                        "status": self._status.value
                    }
                }, node=self.node.name))

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, maintenance: bool):
        self.set_maintenance(maintenance)

    def set_maintenance(self, maintenance: Union[str, bool]):
        if isinstance(maintenance, str):
            new_maintenance = maintenance.lower() == 'true'
        else:
            new_maintenance = maintenance
        if new_maintenance != self._maintenance:
            self._maintenance = new_maintenance
            if not isinstance(maintenance, str) and not (self.node.master and not self.is_remote):
                self.bus.loop.create_task(self.bus.send_to_node({
                    "command": "rpc",
                    "object": "Server",
                    "params": {
                        "maintenance": str(maintenance)
                    },
                    "server_name": self.name
                }, node=self.node.name))
            else:
                self.update_maintenance()

    def update_maintenance(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("UPDATE servers SET maintenance = %s WHERE server_name = %s",
                             (self._maintenance, self.name))

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    @property
    def coalitions(self) -> bool:
        return self.locals.get('coalitions') is not None

    async def get_missions_dir(self) -> str:
        raise NotImplementedError()

    def add_player(self, player: Player):
        self.players[player.id] = player

    def get_player(self, **kwargs) -> Optional[Player]:
        if 'id' in kwargs:
            return self.players.get(kwargs['id'])
        for player in self.players.values():
            if player.id == 1:
                continue
            if kwargs.get('active') is not None and player.active != kwargs['active']:
                continue
            if 'ucid' in kwargs and player.ucid == kwargs['ucid']:
                return player
            if 'discord_id' in kwargs and player.member and player.member.id == kwargs['discord_id']:
                return player
            if 'unit_id' in kwargs and player.unit_id == kwargs['unit_id']:
                return player
            if 'name' in kwargs and player.name == kwargs['name']:
                return player
            if 'ipaddr' in kwargs and player.ipaddr == kwargs['ipaddr']:
                return player
        return None

    def get_active_players(self, *, side: Side = None) -> list[Player]:
        return [x for x in self.players.values() if x.active and (not side or side == x.side)]

    def get_crew_members(self, pilot: Player) -> list[Player]:
        members = []
        if pilot:
            # now find players that have the same slot
            for player in self.players.values():
                if player.active and player.slot == pilot.slot:
                    members.append(player)
        return members

    def is_populated(self) -> bool:
        if self.status in [Status.RUNNING, Status.PAUSED] and self.get_active_players():
            return True
        return False

    def is_public(self) -> bool:
        if self.settings.get('password'):
            return False
        else:
            return True

    async def move_to_spectators(self, player: Player, reason: str = 'n/a'):
        await self.send_to_dcs({
            "command": "force_player_slot",
            "playerID": player.id,
            "sideID": 0,
            "slotID": "",
            "reason": reason
        })

    async def kick(self, player: Player, reason: str = 'n/a'):
        await self.send_to_dcs({
            "command": "kick",
            "id": player.id,
            "reason": reason
        })

    @property
    def settings(self) -> dict:
        raise NotImplementedError()

    @property
    def options(self) -> dict:
        raise NotImplementedError()

    async def get_current_mission_file(self) -> Optional[str]:
        raise NotImplementedError()

    async def get_current_mission_theatre(self) -> Optional[str]:
        raise NotImplementedError()

    async def send_to_dcs(self, message: dict):
        raise NotImplementedError()

    async def rename(self, new_name: str, update_settings: bool = False) -> None:
        raise NotImplementedError()

    async def startup(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = True) -> None:
        raise NotImplementedError()

    async def send_to_dcs_sync(self, message: dict, timeout: Optional[int] = 5.0) -> Optional[dict]:
        with PerformanceLog(f"DCS: dcsbot.{message['command']}()"):
            future = self.bus.loop.create_future()
            token = 'sync-' + str(uuid.uuid4())
            message['channel'] = token
            self.listeners[token] = future
            try:
                await self.send_to_dcs(message)
                return await asyncio.wait_for(future, timeout)
            finally:
                del self.listeners[token]

    async def sendChatMessage(self, coalition: Coalition, message: str, sender: str = None):
        if coalition == Coalition.ALL:
            for msg in message.split('\n'):
                await self.send_to_dcs({
                    "command": "sendChatMessage",
                    "from": sender,
                    "message": msg
                })
        else:
            raise NotImplementedError()

    async def sendPopupMessage(self, recipient: Union[Coalition, str], message: str, timeout: Optional[int] = -1,
                               sender: str = None):
        if timeout == -1:
            timeout = self.locals.get('message_timeout', 10)
        await self.send_to_dcs({
            "command": "sendPopupMessage",
            "to": 'coalition' if isinstance(recipient, Coalition) else 'group',
            "id": recipient.value if isinstance(recipient, Coalition) else recipient,
            "from": sender,
            "message": message,
            "time": timeout
        })

    async def playSound(self, recipient: Union[Coalition, str], sound: str):
        await self.send_to_dcs({
            "command": "playSound",
            "to": 'coalition' if isinstance(recipient, Coalition) else 'group',
            "id": recipient.value if isinstance(recipient, Coalition) else recipient,
            "sound": sound
        })

    async def lock(self, message: Optional[str] = None):
        await self.send_to_dcs({
            "command": "lock_server",
            "message": message
        })

    async def unlock(self):
        await self.send_to_dcs({
            "command": "unlock_server"
        })

    async def stop(self) -> None:
        raise NotImplementedError()

    @performance_log()
    async def start(self) -> bool:
        if self.status == Status.STOPPED:
            timeout = 300 if self.node.locals.get('slow_system', False) else 180
            self.status = Status.LOADING
            rc = await self.send_to_dcs_sync({"command": "start_server"})
            if rc['result'] == 0:
                await self.wait_for_status_change([Status.PAUSED, Status.RUNNING], timeout)
                return True
        return False

    async def restart(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = True) -> None:
        raise NotImplementedError()

    async def setStartIndex(self, mission_id: int) -> None:
        raise NotImplementedError()

    async def setPassword(self, password: str):
        raise NotImplementedError()

    async def setCoalitionPassword(self, coalition: Coalition, password: str):
        raise NotImplementedError()

    async def addMission(self, path: str, *, idx: Optional[int] = -1, autostart: Optional[bool] = False) -> list[str]:
        raise NotImplementedError()

    async def deleteMission(self, mission_id: int) -> list[str]:
        raise NotImplementedError()

    async def replaceMission(self, mission_id: int, path: str) -> list[str]:
        raise NotImplementedError()

    async def loadMission(self, mission: Union[int, str], modify_mission: Optional[bool] = True,
                          use_orig: Optional[bool] = True, no_reload: Optional[bool] = False) -> Optional[bool]:
        raise NotImplementedError()

    async def loadNextMission(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = True) -> bool:
        raise NotImplementedError()

    async def getMissionList(self) -> list[str]:
        raise NotImplementedError()

    async def getAllMissionFiles(self) -> list[str]:
        raise NotImplementedError()

    async def modifyMission(self, filename: str, preset: Union[list, dict], use_orig: bool = True) -> str:
        raise NotImplementedError()

    async def uploadMission(self, filename: str, url: str, *, missions_dir: str = None, force: bool = False,
                            orig = False) -> UploadStatus:
        raise NotImplementedError()

    async def apply_mission_changes(self, filename: Optional[str] = None, use_orig: Optional[bool] = True) -> str:
        raise NotImplementedError()

    @property
    def channels(self) -> dict[Channel, int]:
        if not self._channels:
            if 'channels' not in self.locals and self.name != 'n/a':
                self.log.warning(f"No channels defined in servers.yaml for server {self.name}!")
            self._channels = {}
            for key, value in self.locals.get('channels', {}).items():
                self._channels[Channel(key)] = int(value)
            if Channel.STATUS not in self._channels:
                self._channels[Channel.STATUS] = -1
            if Channel.CHAT not in self._channels:
                self._channels[Channel.CHAT] = -1
            if Channel.EVENTS not in self._channels:
                self._channels[Channel.EVENTS] = self._channels[Channel.CHAT]
            if Channel.VOICE not in self._channels:
                self._channels[Channel.VOICE] = -1
            if Channel.COALITION_BLUE_EVENTS not in self._channels and Channel.COALITION_BLUE_CHAT in self._channels:
                self._channels[Channel.COALITION_BLUE_EVENTS] = self._channels[Channel.COALITION_BLUE_CHAT]
            if Channel.COALITION_RED_EVENTS not in self._channels and Channel.COALITION_RED_CHAT in self._channels:
                self._channels[Channel.COALITION_RED_EVENTS] = self._channels[Channel.COALITION_RED_CHAT]
        return self._channels

    async def update_channels(self, channels: dict[str, int]) -> None:
        raise NotImplementedError()

    async def wait_for_status_change(self, status: list[Status], timeout: int = 60) -> None:
        async def wait(s: list[Status]):
            while self.status not in s:
                await self.status_change.wait()

        if self.status not in status:
            await asyncio.wait_for(wait(status), timeout)

    async def shutdown(self, force: bool = False) -> None:
        raise NotImplementedError()

    async def init_extensions(self) -> list[str]:
        raise NotImplementedError()

    async def prepare_extensions(self):
        raise NotImplementedError()

    async def persist_settings(self):
        raise NotImplementedError()

    async def render_extensions(self) -> list[dict]:
        raise NotImplementedError()

    async def is_running(self) -> bool:
        raise NotImplementedError()

    async def run_on_extension(self, extension: str, method: str, **kwargs) -> Any:
        raise NotImplementedError()

    async def config_extension(self, name: str, config: dict) -> None:
        raise NotImplementedError()

    async def install_extension(self, name: str, config: dict) -> None:
        raise NotImplementedError()

    async def uninstall_extension(self, name: str) -> None:
        raise NotImplementedError()

    async def cleanup(self) -> None:
        raise NotImplementedError()

    async def install_plugin(self, plugin: str) -> None:
        raise NotImplementedError()

    async def uninstall_plugin(self, plugin: str) -> None:
        raise NotImplementedError()

    @async_cache
    async def list_extension(self) -> list[str]:
        return self.locals.get('extensions', [])
