from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys

if sys.platform == 'win32':
    import win32con
    import win32gui

from collections import OrderedDict
from contextlib import suppress
from copy import deepcopy
from core import utils, Server
from core.data.dataobject import DataObjectFactory
from core.data.const import Status, Channel
from core.mizfile import MizFile, UnsupportedMizFileException
from core.data.node import UploadStatus
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePath
from psutil import Process
from typing import Optional, TYPE_CHECKING, Union, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from core import Extension, Instance
    from services import DCSServerBot
    from watchdog.events import FileSystemEvent, FileSystemMovedEvent

__all__ = ["ServerImpl"]


class MissionFileSystemEventHandler(FileSystemEventHandler):
    def __init__(self, server: Server):
        self.server = server
        self.log = server.log

    def on_created(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if path.endswith('.miz'):
            if self.server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                self.server.send_to_dcs({"command": "addMission", "path": path})
            else:
                missions = self.server.settings['missionList']
                missions.append(path)
            self.log.info(f"=> New mission {os.path.basename(path)[:-4]} added to server {self.server.name}.")

    def on_moved(self, event: FileSystemMovedEvent):
        self.on_deleted(event)
        self.on_created(FileSystemEvent(event.dest_path))

    def on_deleted(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if not path.endswith('.miz'):
            return
        missions = self.server.settings['missionList']
        if path in missions:
            if self.server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                idx = missions.index(path) + 1
                if idx == self.server.mission_id:
                    self.log.fatal(f'The running mission on server {self.server.name} got deleted!')
                    return
                else:
                    self.server.send_to_dcs({"command": "deleteMission", "id": idx})
            else:
                missions.remove(path)
                self.server.settings['missionList'] = missions
            self.log.info(f"=> Mission {os.path.basename(path)[:-4]} deleted from server {self.server.name}.")


@dataclass
@DataObjectFactory.register()
class ServerImpl(Server):
    bot: Optional[DCSServerBot] = field(compare=False, init=False)
    event_handler: MissionFileSystemEventHandler = field(compare=False, default=None)
    observer: Observer = field(compare=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        self.lock = asyncio.Lock()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("INSERT INTO servers (server_name) VALUES (%s) ON CONFLICT DO NOTHING", (self.name, ))
            row = conn.execute("SELECT maintenance FROM servers WHERE server_name = %s", (self.name,)).fetchone()
            if row:
                self._maintenance = row[0]

    async def reload(self):
        self.locals = self.read_locals()
        self._channels.clear()
        self._options = None
        self._settings = None
        self.prepare()

    @property
    def is_remote(self) -> bool:
        return False

    async def get_missions_dir(self) -> str:
        return self.instance.missions_dir

    @property
    def settings(self) -> dict:
        if not self._settings:
            path = os.path.join(self.instance.home, 'Config', 'serverSettings.lua')
            self._settings = utils.SettingsDict(self, path, 'cfg')
            # TODO: can be removed if bug in net.load_next_mission() is fixed
            if self._settings.get('listLoop', False):
                self._settings['listLoop'] = True
            # if someone managed to destroy the mission list, fix it...
            if 'missionList' not in self._settings:
                self._settings['missionList'] = []
            elif isinstance(self._settings['missionList'], dict):
                self._settings['missionList'] = list(self._settings['missionList'].values())
        return self._settings

    @property
    def options(self) -> dict:
        if not self._options:
            path = os.path.join(self.instance.home, 'Config', 'options.lua')
            self._options = utils.SettingsDict(self, path, 'options')
            # no options.lua, create a minimalistic one
            if 'graphics' not in self._options:
                self._options["graphics"] = {
                    "visibRange": "High"
                }
            if 'plugins' not in self._options:
                self._options["plugins"] = {}
            if 'difficulty' not in self._options:
                self._options["difficulty"] = {}
            if 'miscellaneous' not in self._options:
                self._options["miscellaneous"] = {}
        return self._options

    def set_instance(self, instance: Instance):
        self._instance = instance
        self.locals |= self.instance.locals
        if self.name != 'n/a':
            self.prepare()

    def set_status(self, status: Union[Status, str]):
        if status != self._status:
            if self.locals.get('autoscan', False):
                if (self._status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTDOWN]
                        and status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]):
                    if not self.observer.emitters:
                        self.observer.schedule(self.event_handler, self.instance.missions_dir, recursive=False)
                        self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder enabled.')
                elif status == Status.SHUTDOWN:
                    if self._status == Status.UNREGISTERED:
                        # make sure all missions in the directory are in the mission list ...
                        directory = Path(self.instance.missions_dir)
                        missions = self.settings['missionList']
                        i: int = 0
                        for file in directory.glob('*.miz'):
                            secondary = os.path.join(os.path.dirname(file), '.dcssb', os.path.basename(file))
                            if str(file) not in missions and secondary not in missions:
                                missions.append(str(file))
                                i += 1
                        # make sure the list is written to serverSettings.lua
                        self.settings['missionList'] = missions
                        if i:
                            self.log.info(f"  => {self.name}: {i} missions auto-added to the mission list")
                    elif self.observer.emitters:
                        self.observer.unschedule_all()
                        self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder disabled.')
            elif self._status == Status.UNREGISTERED and status == Status.SHUTDOWN:
                # make sure, mission names are unique
                current_mission = self._get_current_mission_file()
                if current_mission:
                    self._settings['missionList'] = list(
                        OrderedDict.fromkeys(os.path.normpath(x) for x in self._settings['missionList']).keys()
                    )
                    try:
                        new_start = self._settings['missionList'].index(current_mission)
                    except ValueError:
                        new_start = 0
                    self._settings['listStartIndex'] = new_start + 1
            super().set_status(status)

    def _install_luas(self):
        dcs_path = os.path.join(self.instance.home, 'Scripts')
        if not os.path.exists(dcs_path):
            os.mkdir(dcs_path)
        ignore = None
        bot_home = os.path.join(dcs_path, 'net', 'DCSServerBot')
        if os.path.exists(bot_home):
            self.log.debug('  - Updating Hooks ...')
            utils.safe_rmtree(bot_home)
            ignore = shutil.ignore_patterns('DCSServerBotConfig.lua.tmpl')
        else:
            self.log.debug('  - Installing Hooks ...')
        shutil.copytree('Scripts', dcs_path, dirs_exist_ok=True, ignore=ignore)
        try:
            admin_channel = self.channels.get(Channel.ADMIN)
            if not admin_channel:
                data = yaml.load(Path(os.path.join(self.node.config_dir, 'services', 'bot.yaml')))
                admin_channel = data.get('admin_channel', -1)
            with open(os.path.join('Scripts', 'net', 'DCSServerBot', 'DCSServerBotConfig.lua.tmpl'), mode='r',
                      encoding='utf-8') as template:
                with open(os.path.join(bot_home, 'DCSServerBotConfig.lua'), mode='w', encoding='utf-8') as outfile:
                    for line in template.readlines():
                        line = utils.format_string(line, node=self.node, instance=self.instance, server=self,
                                                   admin_channel=admin_channel)
                        outfile.write(line)
        except KeyError as k:
            self.log.error(
                f'! You must set a value for {k}. See README for help.')
            raise k
        except Exception as ex:
            self.log.exception(ex)
        self.log.debug(f"  - Installing Plugin luas into {self.instance.name} ...")
        for plugin_name in self.node.plugins:
            source_path = f'./plugins/{plugin_name}/lua'
            if os.path.exists(source_path):
                target_path = os.path.join(bot_home, f'{plugin_name}')
                shutil.copytree(source_path, target_path, dirs_exist_ok=True)
                self.log.debug(f'    => Plugin {plugin_name.capitalize()} installed.')
        self.log.debug(f'  - Luas installed into {self.instance.name}.')

    def prepare(self):
        if self.settings.get('name', 'DCS Server') != self.name:
            self.settings['name'] = self.name
        if 'serverSettings' in self.locals:
            for key, value in self.locals['serverSettings'].items():
                if key == 'advanced':
                    self.settings['advanced'] = self.settings['advanced'] | value
                else:
                    self.settings[key] = value
        self._install_luas()
        # enable autoscan for missions changes
        if self.locals.get('autoscan', False):
            self.event_handler = MissionFileSystemEventHandler(self)
            self.observer = Observer()
            self.observer.start()

    def _get_current_mission_file(self) -> Optional[str]:
        if not self.current_mission or not self.current_mission.filename:
            settings = self.settings
            start_index = int(settings.get('listStartIndex', 1))
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
        return os.path.normpath(filename) if filename else None

    async def get_current_mission_file(self) -> Optional[str]:
        return self._get_current_mission_file()

    async def get_current_mission_theatre(self) -> Optional[str]:
        filename = await self.get_current_mission_file()
        if filename:
            miz = await asyncio.to_thread(MizFile, self.node, filename)
            return miz.theatre

    def serialize(self, message: dict):
        def _serialize_value(value: Any) -> Any:
            if isinstance(value, bool):
                return value
            elif isinstance(value, int):
                return str(value)
            elif isinstance(value, Enum):
                return value.value
            elif isinstance(value, dict):
                return self.serialize(value)
            elif isinstance(value, list):
                return [_serialize_value(x) for x in value]
            return value

        for key, value in message.items():
            message[key] = _serialize_value(value)
        return message

    def send_to_dcs(self, message: dict):
        # As Lua does not support large numbers, convert them to strings
        message = self.serialize(deepcopy(message))
        msg = json.dumps(message)
        self.log.debug(f"HOST->{self.name}: {msg}")
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dcs_socket.sendto(msg.encode('utf-8'), ('127.0.0.1', int(self.port)))
        dcs_socket.close()

    async def rename(self, new_name: str, update_settings: bool = False) -> None:
        def update_config(old_name, new_name: str, update_settings: bool = False):
            # update servers.yaml
            filename = os.path.join(self.node.config_dir, 'servers.yaml')
            if os.path.exists(filename):
                data = yaml.load(Path(filename).read_text(encoding='utf-8'))
                if old_name in data and new_name not in data:
                    data[new_name] = deepcopy(data[old_name])
                    del data[old_name]
                    with open(filename, mode='w', encoding='utf-8') as outfile:
                        yaml.dump(data, outfile)
            # update serverSettings.lua if requested
            if update_settings:
                self.settings['name'] = new_name

        old_name = self.name
        try:
            # rename the server in the database
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    # we need to remove any older server that might have had the same name
                    await conn.execute('DELETE FROM servers WHERE server_name = %s', (new_name, ))
                    await conn.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                                       (new_name, self.name))
                    await conn.execute('DELETE FROM instances WHERE server_name = %s', (new_name, ))
                    await conn.execute('UPDATE instances SET server_name = %s WHERE server_name = %s',
                                       (new_name, self.name))
                    await conn.execute('DELETE FROM message_persistence WHERE server_name = %s', (new_name, ))
                    await conn.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                                       (new_name, self.name))
                    # only the master can take care of a cluster-wide rename
                    if self.node.master:
                        await self.node.rename_server(self, new_name)
                    else:
                        await self.bus.send_to_node_sync({
                            "command": "rpc",
                            "object": "Node",
                            "method": "rename_server",
                            "params": {
                                "server": self.name,
                                "new_name": new_name
                            }
                        })
                        self.bus.rename_server(self, new_name)
            try:
                # update servers.yaml
                update_config(old_name, new_name, update_settings)
                self.name = new_name
            except Exception:
                # rollback config
                update_config(new_name, old_name, update_settings)
                raise
        except Exception:
            self.log.exception(f"Error during renaming of server {old_name} to {new_name}: ", exc_info=True)

    def do_startup(self):
        basepath = self.node.installation
        for exe in ['DCS_server.exe', 'DCS.exe']:
            path = os.path.join(basepath, 'bin', exe)
            if os.path.exists(path):
                break
        else:
            self.log.error(f"No executable found to start a DCS server in {basepath}!")
            return
        # check if all missions are existing
        missions = []
        for mission in self.settings['missionList']:
            if '.dcssb' in mission:
                secondary = os.path.join(os.path.dirname(os.path.dirname(mission)), os.path.basename(mission))
            else:
                secondary = os.path.join(os.path.dirname(mission), '.dcssb', os.path.basename(mission))
            if os.path.exists(mission):
                missions.append(mission)
            elif os.path.exists(secondary):
                missions.append(secondary)
            else:
                self.log.warning(f"Removing mission {mission} from serverSettings.lua as it could not be found!")
        if len(missions) != len(self.settings['missionList']):
            self.settings['missionList'] = missions
            self.log.warning('Removed non-existent missions from serverSettings.lua')
        self.log.debug(r'Launching DCS server with: "{}" --server --norender -w {}'.format(path, self.instance.name))
        try:
            p = subprocess.Popen(
                [exe, '--server', '--norender', '-w', self.instance.name], executable=path, close_fds=True
            )
            self.process = Process(p.pid)
            self.log.debug(f"  => DCS server starting up with PID {p.pid}")
        except Exception:
            self.log.error(f"  => Error while trying to launch DCS!", exc_info=True)
            self.process = None

    async def init_extensions(self):
        for extension in self.locals.get('extensions', {}):
            try:
                ext: Extension = self.extensions.get(extension)
                if not ext:
                    if '.' not in extension:
                        _extension = 'extensions.' + extension
                    else:
                        _extension = extension
                    _ext = utils.str_to_class(_extension)
                    if not _ext:
                        self.log.error(f"Extension {extension} could not be found!")
                        return
                    ext = _ext(
                        self,
                        self.node.locals.get('extensions', {}).get(extension, {}) | self.locals['extensions'][extension]
                    )
                    if ext.is_installed():
                        self.extensions[extension] = ext
            except Exception as ex:
                self.log.exception(ex)

    @staticmethod
    def _window_enumeration_handler(hwnd, top_windows):
        top_windows.append((hwnd, win32gui.GetWindowText(hwnd)))

    def _minimize(self):
        top_windows = []
        win32gui.EnumWindows(self._window_enumeration_handler, top_windows)

        # Fetch the window name of the process
        window_name = self.instance.name

        for i in top_windows:
            if window_name.lower() in i[1].lower():
                win32gui.ShowWindow(i[0], win32con.SW_MINIMIZE)
                break

    async def startup(self, modify_mission: Optional[bool] = True) -> None:
        await self.init_extensions()
        for ext in self.extensions.values():
            try:
                await ext.prepare()
            except Exception as ex:
                self.log.error(f"  => Error during {ext.name}.prepare(): {ex}. Skipped.")
        if modify_mission:
            await self.apply_mission_changes()
        await asyncio.to_thread(self.do_startup)
        timeout = 300 if self.node.locals.get('slow_system', False) else 180
        self.status = Status.LOADING
        try:
            await self.wait_for_status_change([Status.SHUTDOWN, Status.STOPPED, Status.PAUSED, Status.RUNNING], timeout)
            if self.status == Status.SHUTDOWN:
                raise TimeoutError()
            if sys.platform == 'win32' and self.node.locals.get('DCS', {}).get('minimized', True):
                self._minimize()
        except (TimeoutError, asyncio.TimeoutError):
            # server crashed during launch?
            if self.status != Status.SHUTDOWN and not await self.is_running():
                self.status = Status.SHUTDOWN
            raise

    async def startup_extensions(self) -> None:
        not_running_extensions = [ext for ext in self.extensions.values() if not ext.is_running()]
        startup_coroutines = [ext.startup() for ext in not_running_extensions]

        results = await asyncio.gather(*startup_coroutines, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                self.log.error(f"Error during startup_extension()", exc_info=True)

    async def shutdown_extensions(self) -> None:
        running_extensions = [ext for ext in self.extensions.values() if ext.is_running()]
        shutdown_coroutines = [asyncio.to_thread(ext.shutdown) for ext in running_extensions]

        results = await asyncio.gather(*shutdown_coroutines, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                self.log.error(f"Error during shutdown_extension()", exc_info=True)

    async def shutdown(self, force: bool = False) -> None:
        if await self.is_running():
            if not force:
                await super().shutdown(False)
            self._terminate()
        self.status = Status.SHUTDOWN

    async def is_running(self) -> bool:
        async with self.lock:
            if not self.process or not self.process.is_running():
                self.process = await asyncio.to_thread(utils.find_process, "DCS_server.exe|DCS.exe", self.instance.name)
            return self.process is not None

    def _terminate(self) -> None:
        if self.process and self.process.is_running():
            self.process.terminate()
            if self.process.is_running():
                self.process.kill()
        self.process = None

    async def apply_mission_changes(self, filename: Optional[str] = None) -> str:
        # disable autoscan
        autoscan = self.locals.get('autoscan', False)
        if autoscan:
            self.locals['autoscan'] = False
        if not filename:
            filename = await self.get_current_mission_file()
            if not filename:
                self.log.warning("No mission found. Is your mission list empty?")
                return filename
        new_filename = filename
        try:
            # make a backup
            if '.dcssb' not in filename and not os.path.exists(filename + '.orig'):
                shutil.copy2(filename, filename + '.orig')
            # process all mission modifications
            dirty = False
            for ext in self.extensions.values():
                new_filename, _dirty = await ext.beforeMissionLoad(new_filename)
                if _dirty:
                    self.log.info(f'  => {ext.name} applied on {new_filename}.')
                dirty |= _dirty
            # we did not change anything in the mission
            if not dirty:
                return filename
            # check if the original mission can be written
            if filename != new_filename:
                missions: list[str] = self.settings['missionList']
                index = missions.index(filename) + 1
                await self.replaceMission(index, new_filename)
            return new_filename
        except Exception as ex:
            if isinstance(ex, UnsupportedMizFileException):
                self.log.error(
                    f'The mission {filename} is not compatible with MizEdit. Please re-save it in DCS World.')
            else:
                self.log.error(ex)
            if filename != new_filename and os.path.exists(new_filename):
                os.remove(new_filename)
            return filename
        finally:
            # enable autoscan
            if autoscan:
                self.locals['autoscan'] = True

    async def keep_alive(self):
        self.send_to_dcs({"command": "getMissionUpdate"})
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE instances SET last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE node = %s AND server_name = %s
                """, (self.node.name, self.name))

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        stopped = False
        for idx, name in enumerate(self.settings['missionList']):
            if os.path.basename(name) == filename:
                if self.current_mission and idx == int(self.settings['listStartIndex']) - 1:
                    if not force:
                        return UploadStatus.FILE_IN_USE
                    await self.stop()
                    stopped = True
                filename = name
                break
        else:
            filename = os.path.normpath(os.path.join(self.instance.missions_dir, filename))
        rc = await self.node.write_file(filename, url, force)
        if rc != UploadStatus.OK:
            return rc
        if not self.locals.get('autoscan', False):
            await self.addMission(filename)
        if stopped:
            await self.start()
        return UploadStatus.OK

    async def listAvailableMissions(self) -> list[str]:
        return [str(x) for x in sorted(Path(PurePath(self.instance.missions_dir)).glob("*.miz"))]

    async def getMissionList(self) -> list[str]:
        return self.settings.get('missionList', [])

    async def modifyMission(self, filename: str, preset: Union[list, dict]) -> str:
        async def apply_preset(value: dict):
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
            if 'forcedOptions' in value:
                miz.forcedOptions = value['forcedOptions']
            if 'miscellaneous' in value:
                miz.miscellaneous = value['miscellaneous']
            if 'difficulty' in value:
                miz.difficulty = value['difficulty']
            if 'files' in value:
                miz.files = value['files']
            if 'modify' in value:
                miz.modify(value['modify'])

        miz = await asyncio.to_thread(MizFile, self, filename)
        if isinstance(preset, list):
            for p in preset:
                if not isinstance(p, dict):
                    self.log.error(f"{p} is not a dictionary!")
                    continue
                await apply_preset(p)
        elif isinstance(preset, dict):
            await apply_preset(preset)
        else:
            self.log.error(f"{preset} is not a dictionary!")
        # write new mission
        new_filename = utils.create_writable_mission(filename)
        await asyncio.to_thread(miz.save, new_filename)
        return new_filename

    async def persist_settings(self):
        config_file = os.path.join(self.node.config_dir, 'servers.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        if self.name not in config:
            config[self.name] = {}
        config[self.name]['serverSettings'] = {
            "description": self.settings.get('description', ''),
            "advanced": self.settings.get('advanced', {}),
            "mode": self.settings.get('mode', '0'),
            "isPublic": self.settings.get('isPublic', True),
            "name": self.name,
            "password": self.settings.get('password', ''),
            "require_pure_textures": self.settings.get('require_pure_textures', True),
            "require_pure_scripts": self.settings.get('require_pure_scripts', True),
            "require_pure_clients": self.settings.get('require_pure_clients', True),
            "require_pure_models": self.settings.get('require_pure_models', True),
            "maxPlayers": self.settings.get('maxPlayers', 16)
        }
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)

    async def render_extensions(self) -> list[dict]:
        ret: list[dict] = []
        for ext in self.extensions.values():
            with suppress(NotImplementedError):
                ret.append(await ext.render())
        return ret

    async def restart(self, modify_mission: Optional[bool] = True) -> None:
        await self.loadMission(int(self.settings['listStartIndex']), modify_mission=modify_mission)

    async def setStartIndex(self, mission_id: int) -> None:
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            self.send_to_dcs({"command": "setStartIndex", "id": mission_id})
        else:
            self.settings['listStartIndex'] = mission_id

    async def addMission(self, path: str, *, autostart: Optional[bool] = False) -> None:
        path = os.path.normpath(path)
        if '.dcssb' in path:
            secondary = os.path.join(os.path.dirname(os.path.dirname(path)), os.path.basename(path))
        else:
            secondary = os.path.join(os.path.dirname(path), '.dcssb', os.path.basename(path))
        missions = self.settings['missionList']
        if path in missions or secondary in missions:
            return
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            data = await self.send_to_dcs_sync({"command": "addMission", "path": path, "autostart": autostart})
            self.settings['missionList'] = data['missionList']
        else:
            missions.append(path)
            self.settings['missionList'] = missions
            if autostart:
                self.settings['listStartIndex'] = missions.index(path if path in missions else secondary) + 1

    async def deleteMission(self, mission_id: int) -> None:
        if self.status in [Status.PAUSED, Status.RUNNING] and self.mission_id == mission_id:
            raise AttributeError("Can't delete the running mission!")
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            data = await self.send_to_dcs_sync({"command": "deleteMission", "id": mission_id})
            self.settings['missionList'] = data['missionList']
        else:
            missions = self.settings['missionList']
            del missions[mission_id - 1]
            self.settings['missionList'] = missions

    async def replaceMission(self, mission_id: int, path: str) -> None:
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            await self.send_to_dcs_sync({"command": "replaceMission", "index": mission_id, "path": path})
        else:
            missions: list[str] = self.settings['missionList']
            missions[mission_id - 1] = path
            self.settings['missionList'] = missions

    async def loadMission(self, mission: Union[int, str], modify_mission: Optional[bool] = True) -> None:
        if isinstance(mission, int):
            if mission > len(self.settings['missionList']):
                mission = 1
            filename = self.settings['missionList'][mission - 1]
        else:
            filename = mission
        if modify_mission:
            filename = await self.apply_mission_changes(filename)
        stopped = self.status == Status.STOPPED
        try:
            idx = self.settings['missionList'].index(filename) + 1
            if idx == int(self.settings['listStartIndex']):
                self.send_to_dcs({"command": "startMission", "filename": filename})
            else:
                self.send_to_dcs({"command": "startMission", "id": idx})
        except ValueError:
            self.send_to_dcs({"command": "startMission", "filename": filename})
        if not stopped:
            # wait for a status change (STOPPED or LOADING)
            await self.wait_for_status_change([Status.STOPPED, Status.LOADING], timeout=120)
        else:
            self.send_to_dcs({"command": "start_server"})
        # wait until we are running again
        await self.wait_for_status_change([Status.RUNNING, Status.PAUSED], timeout=300)

    async def loadNextMission(self, modify_mission: Optional[bool] = True) -> None:
        await self.loadMission(int(self.settings['listStartIndex']) + 1, modify_mission)
