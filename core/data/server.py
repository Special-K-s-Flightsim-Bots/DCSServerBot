from __future__ import annotations
import asyncio
from contextlib import suppress

import discord
import os
import platform
import uuid
from core import utils
from dataclasses import dataclass, field
from datetime import datetime
from psutil import Process
from typing import Optional, Union, TYPE_CHECKING
from .dataobject import DataObject
from .const import Status, Coalition, Channel

if TYPE_CHECKING:
    from core import Player, Mission, Extension


@dataclass
class Server(DataObject):
    name: str = field(compare=False)
    installation: str
    host: str
    port: int
    _channels: dict[Channel, discord.TextChannel] = field(default_factory=dict, compare=False)
    embeds: dict[str, Union[int, discord.Message]] = field(repr=False, default_factory=dict, compare=False)
    _status: Status = field(default=Status.UNREGISTERED, compare=False)
    status_change: asyncio.Event = field(compare=False, init=False)
    _options: Optional[utils.SettingsDict] = field(default=None, compare=False)
    _settings: Optional[utils.SettingsDict] = field(default=None, compare=False)
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
    listeners: dict[str, asyncio.Future] = field(default_factory=dict, compare=False)

    def __post_init__(self):
        super().__post_init__()
        self._lock = asyncio.Lock()
        self.status_change = asyncio.Event()
        with self.pool.connection() as conn:
            # read persisted messages for this server
            for row in conn.execute('SELECT embed_name, embed FROM message_persistence WHERE server_name = %s',
                                    (self.name, )).fetchall():
                self.embeds[row[0]] = row[1]

    @property
    def is_remote(self) -> bool:
        raise NotImplemented()

    @property
    def status(self) -> Status:
        return self._status

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    @status.setter
    def status(self, status: Status):
        if status != self._status:
            self.log.info(f"Server {self.name} changing status from {self._status.name} to {status.name}")
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

    def move_to_spectators(self, player: Player, reason: str = 'n/a'):
        self.sendtoDCS({
            "command": "force_player_slot",
            "playerID": player.id,
            "sideID": 0,
            "slotID": "",
            "reason": reason
        })

    def kick(self, player: Player, reason: str = 'n/a'):
        self.sendtoDCS({
            "command": "kick",
            "id": player.id,
            "reason": reason
        })

    @property
    def settings(self) -> dict:
        raise NotImplemented()

    @property
    def options(self) -> dict:
        raise NotImplemented()

    async def get_current_mission_file(self) -> Optional[str]:
        raise NotImplemented()

    def sendtoDCS(self, message: dict):
        raise NotImplemented()

    async def sendtoDCSSync(self, message: dict, timeout: Optional[int] = 5.0):
        future = self.bot.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.listeners[token] = future
        try:
            self.sendtoDCS(message)
            return await asyncio.wait_for(future, timeout)
        finally:
            del self.listeners[token]

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
        raise NotImplemented()

    async def startup(self) -> None:
        raise NotImplemented()

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
        raise NotImplemented()

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
                    if not file:
                        await message.edit(embed=embed)
                    else:
                        await message.edit(embed=embed, attachments=[file])
                except discord.errors.NotFound:
                    message = None
                except Exception as ex:
                    self.log.warning(f"Error during update of embed {embed_name}: " + str(ex))
                    return
            if not message:
                message = await channel.send(embed=embed, file=file)
                self.embeds[embed_name] = message
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                            INSERT INTO message_persistence (server_name, embed_name, embed) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (server_name, embed_name) 
                            DO UPDATE SET embed=excluded.embed
                        """, (self.name, embed_name, message.id))

    async def keep_alive(self):
        # we set a longer timeout in here because, we don't want to risk false restarts
        timeout = 20 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 10
        data = await self.sendtoDCSSync({"command": "getMissionUpdate"}, timeout)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE servers SET last_seen = NOW() WHERE agent_host = %s AND server_name = %s',
                             (platform.node(), self.name))
        if data['pause'] and self.status != Status.PAUSED:
            self.status = Status.PAUSED
        elif not data['pause'] and self.status != Status.RUNNING:
            self.status = Status.RUNNING
        self.current_mission.mission_time = data['mission_time']
        self.current_mission.real_time = data['real_time']

    async def shutdown(self, force: bool = False) -> None:
        slow_system = self.bot.config.getboolean('BOT', 'SLOW_SYSTEM')
        timeout = 300 if slow_system else 180
        self.sendtoDCS({"command": "shutdown"})
        with suppress(asyncio.TimeoutError):
            await self.wait_for_status_change([Status.STOPPED], timeout)
