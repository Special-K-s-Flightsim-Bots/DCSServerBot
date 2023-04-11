from __future__ import annotations
import asyncio
import discord
import json
import os
import psutil
import socket
import subprocess
import win32con
from contextlib import closing, suppress
from core import utils, Server
from dataclasses import dataclass
from psutil import Process
from typing import Optional, Union, TYPE_CHECKING
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from .dataobject import DataObjectFactory
from .const import Status, Channel

if TYPE_CHECKING:
    from core import Plugin


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


@dataclass
@DataObjectFactory.register("Server")
class ServerImpl(Server):

    def __post_init__(self):
        super().__post_init__()
        # enable autoscan for missions changes
        if self.bot.config.getboolean(self.installation, 'AUTOSCAN'):
            self.event_handler = MissionFileSystemEventHandler(self)
            self.observer = Observer()
            self.observer.start()
        if self.bot.config.getboolean('BOT', 'DESANITIZE'):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.expandvars(self.bot.config[self.installation]['DCS_HOME'] + f'\\Scripts\\net\\Slmodv7_{version}\\SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def missions_dir(self) -> str:
        if 'MISSIONS_DIR' in self.bot.config[self.installation]:
            return os.path.expandvars(self.bot.config[self.installation]['MISSIONS_DIR'])
        else:
            return os.path.expandvars(self.bot.config[self.installation]['DCS_HOME']) + os.path.sep + 'Missions'

    @property
    def settings(self) -> dict:
        if not self._settings:
            path = os.path.expandvars(self.bot.config[self.installation]['DCS_HOME']) + r'\Config\serverSettings.lua'
            self._settings = utils.SettingsDict(self, path, 'cfg')
        return self._settings

    @property
    def options(self) -> dict:
        if not self._options:
            path = os.path.expandvars(self.bot.config[self.installation]['DCS_HOME']) + r'\Config\options.lua'
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
        # call rename() in all Plugins
        for plugin in self.bot.cogs.values():  # type: Plugin
            plugin.rename(self.name, new_name)
        # rename the entries in the main database tables
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    cursor.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                                   (new_name, self.name))
                    cursor.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                                   (new_name, self.name))
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
        # check if all missions are existing
        missions = [x for x in self.settings['missionList'] if os.path.exists(x)]
        if len(missions) != len(self.settings['missionList']):
            self.settings['missionList'] = missions
            self.log.warning('Removed non-existent missions from serverSettings.lua')
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

    async def shutdown(self, force: bool = False) -> None:
        slow_system = self.bot.config.getboolean('BOT', 'SLOW_SYSTEM')
        timeout = 300 if slow_system else 180
        if not force:
            self.sendtoDCS({"command": "shutdown"})
            with suppress(asyncio.TimeoutError):
                await self.wait_for_status_change([Status.STOPPED], timeout)
            if self.process and self.process.is_running():
                try:
                    self.process.wait(timeout)
                except psutil.TimeoutExpired:
                    self.process.kill()
        else:
            if self.process and self.process.is_running():
                self.process.kill()
        # make sure, Windows did all cleanups
        if slow_system:
            await asyncio.sleep(10)
        if self.status != Status.SHUTDOWN:
            self.status = Status.SHUTDOWN
        self.process = None

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
