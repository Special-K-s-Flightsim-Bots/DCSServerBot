from __future__ import annotations
import asyncio
import json
import discord
import os
import re
import socket
import subprocess
import psycopg2
import uuid
from contextlib import closing, suppress
from core import utils, const
from dataclasses import dataclass, field
from typing import Optional, Union, TYPE_CHECKING
from .dataobject import DataObject, DataObjectFactory
from .const import Status, Coalition, Channel

if TYPE_CHECKING:
    from .player import Player
    from .mission import Mission
    from ..extension import Extension


@dataclass
@DataObjectFactory.register("Server")
class Server(DataObject):
    name: str = field(compare=False)
    installation: str
    host: str
    port: int
    _channels: dict[Channel, discord.TextChannel] = field(default_factory=dict, compare=False)
    embeds: dict[str, Union[int, discord.Message]] = field(repr=False, default_factory=dict, compare=False)
    status: Status = field(compare=False, default=Status.UNREGISTERED)
    options: dict = field(default_factory=dict, compare=False)
    settings: dict = field(default_factory=dict, compare=False)
    current_mission: Mission = field(default=None, compare=False)
    mission_id: int = field(default=-1, compare=False)
    players: dict[int, Player] = field(default_factory=dict, compare=False)
    pid: int = field(default=-1, compare=False)
    maintenance: bool = field(default=False, compare=False)
    restart_pending: bool = field(default=False, compare=False)
    dcs_version: str = field(default=None, compare=False)
    extensions: dict[str, Extension] = field(default_factory=dict, compare=False)
    lock: asyncio.Lock = field(init=False, compare=False)

    def __post_init__(self):
        super().__post_init__()
        self.lock = asyncio.Lock()
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

    def add_player(self, player: Player):
        self.players[player.id] = player

    def get_player(self, **kwargs) -> Optional[Player]:
        if 'id' in kwargs:
            if kwargs['id'] in self.players:
                return self.players[kwargs['id']]
            else:
                return None
        for player in self.players.values():
            if 'active' in kwargs and player.active != kwargs['active']:
                continue
            if 'ucid' in kwargs and player.ucid == kwargs['ucid']:
                return player
            if 'name' in kwargs and player.name == kwargs['name']:
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
            "ucid": player.ucid,
            "reason": reason
        })

    def changeServerSettings(self, name: str, value: Union[str, int, bool]):
        assert name in ['listStartIndex', 'password', 'name', 'maxPlayers', 'listLoop', 'allow_players_pool'], "Value can't be changed."
        if isinstance(value, str):
            value = '"' + value + '"'
        elif isinstance(value, bool):
            value = value.__repr__().lower()
        _, installation = utils.findDCSInstallations(self.name)[0]
        server_settings = os.path.join(const.SAVED_GAMES, installation, 'Config\\serverSettings.lua')
        tmp_settings = os.path.join(const.SAVED_GAMES, installation, 'Config\\serverSettings.tmp')
        with open(server_settings, encoding='utf8') as infile:
            inlines = infile.readlines()
        outlines = []
        for line in inlines:
            if '["{}"]'.format(name) in line:
                outlines.append(re.sub(' = ([^,]*)', ' = {}'.format(value), line))
                if line.startswith('cfg'):
                    outlines.append('\n')
            else:
                outlines.append(line)
        with open(tmp_settings, 'w', encoding='utf8') as outfile:
            outfile.writelines(outlines)
        os.remove(server_settings)
        os.rename(tmp_settings, server_settings)

    def getServerSetting(self, name: Union[str, int]):
        if isinstance(name, str):
            name = '"' + name + '"'
        exp = re.compile(r'\[{}\] = (?P<value>.*),'.format(name))
        _, installation = utils.findDCSInstallations(self.name)[0]
        server_settings = os.path.join(const.SAVED_GAMES, installation, 'Config\\serverSettings.lua')
        with open(server_settings, encoding='utf8') as infile:
            for line in infile.readlines():
                match = exp.search(line)
                if match:
                    retval = match.group('value')
                    if retval.startswith('"'):
                        return retval.replace('"', '')
                    elif retval == 'false':
                        return False
                    elif retval == 'true':
                        return True
                    else:
                        return int(retval)

    def sendtoDCS(self, message: dict):
        # As Lua does not support large numbers, convert them to strings
        for key, value in message.items():
            if type(value) == int:
                message[key] = str(value)
        msg = json.dumps(message)
        self.log.debug(f"HOST->{self.name}: {msg}")
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dcs_socket.sendto(msg.encode('utf-8'), (self.host, int(self.port)))

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

    def rename(self, old_name: str, new_name: str, update_settings: bool = False) -> None:
        # call rename() in all Plugins
        for plugin in self.bot.cogs.values():
            plugin.rename(old_name, new_name)
        # rename the entries in the main database tables
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
                cursor.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        if update_settings:
            self.changeServerSettings('name', new_name)
        self.name = new_name

    def startup(self):
        self.log.debug(r'Launching DCS server with: "{}\bin\dcs.exe" --server --norender -w {}'.format(
            os.path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']), self.installation))
        p = subprocess.Popen(['dcs.exe', '--server', '--norender', '-w', self.installation],
                             executable=os.path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']) + r'\bin\dcs.exe')
        self.pid = p.pid
        self.status = Status.LOADING
        return p

    async def shutdown(self, timeout: int = -1):
        self.sendtoDCS({"command": "shutdown"})
        if timeout == -1:
            timeout = 300 if self.bot.config['BOT']['SLOW_SYSTEM'] else 120
        for i in range(0, timeout):
            await asyncio.sleep(1)
            if self.status == Status.STOPPED:
                break
        p = utils.find_process('DCS.exe', self.installation)
        if p:
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                p.kill()
        self.pid = -1
        self.status = Status.SHUTDOWN

    def stop(self):
        self.sendtoDCS({"command": "stop_server"})

    def start(self):
        self.sendtoDCS({"command": "start_server"})

    def loadMission(self, mission_id: int):
        self.sendtoDCS({"command": "startMission", "id": mission_id})

    def loadNextMission(self):
        self.sendtoDCS({"command": "startNextMission"})

    async def setEmbed(self, embed_name: str, embed: discord.Embed, file: Optional[discord.File] = None,
                       channel_id: Optional[Union[Channel, int]] = Channel.STATUS):
        async with self.lock:
            message = None
            channel = self.bot.get_channel(channel_id) if isinstance(channel_id, int) else self.get_channel(channel_id)
            if embed_name in self.embeds:
                if isinstance(self.embeds[embed_name],  discord.Message):
                    message = self.embeds[embed_name]
                else:
                    with suppress(discord.errors.NotFound):
                        message = await channel.fetch_message(self.embeds[embed_name])
                        self.embeds[embed_name] = message
            if message:
                try:
                    await message.edit(embed=embed)
                except discord.errors.NotFound:
                    message = None
            if not message:
                message = await channel.send(embed=embed, file=file)
                self.embeds[embed_name] = message
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO message_persistence (server_name, embed_name, embed) VALUES (%s, '
                                       '%s, %s) ON CONFLICT (server_name, embed_name) DO UPDATE SET embed=%s',
                                       (self.name, embed_name, message.id, message.id))
                    conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)

    def get_channel(self, channel: Channel):
        if channel not in self._channels:
            self._channels[channel] = self.bot.get_channel(int(self.bot.config[self.installation][channel.value]))
        return self._channels[channel]
