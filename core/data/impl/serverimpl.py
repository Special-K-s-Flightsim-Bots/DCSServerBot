from __future__ import annotations

import asyncio
import atexit
import json
import os
import psutil
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback

if sys.platform == 'win32':
    import win32con
    import win32gui

from collections import OrderedDict
from contextlib import suppress
from copy import deepcopy
from core import utils, Server
from core.data.dataobject import DataObjectFactory
from core.data.const import Status, Channel, Coalition
from core.extension import Extension, InstallException, UninstallException
from core.mizfile import MizFile
from core.data.node import UploadStatus
from core.utils.performance import performance_log
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Union, Any
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileSystemMovedEvent
from watchdog.observers import Observer, ObserverType

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from core import Instance
    from services.bot import DCSServerBot

DEFAULT_EXTENSIONS = {
    "LogAnalyser": {},
    "Cloud": {}
}

__all__ = ["ServerImpl"]


class MissionFileSystemEventHandler(FileSystemEventHandler):
    def __init__(self, server: Server, loop: asyncio.AbstractEventLoop):
        self.server = server
        self.log = server.log
        self.loop = loop
        self.deleted: dict[str, int] = {}

    def on_created(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        # ignore non-mission files and such that are in the .dcssb folder
        if not (path.endswith('.miz') or path.endswith('.sav')) or '.dcssb' in path:
            return
        if path in self.deleted:
            asyncio.run_coroutine_threadsafe(self.server.addMission(path, idx=self.deleted[path]), self.loop)
            del self.deleted[path]
        else:
            asyncio.run_coroutine_threadsafe(self.server.addMission(path), self.loop)
        self.log.info(f"=> New mission {os.path.basename(path)[:-4]} added to server {self.server.name}.")

    def on_moved(self, event: FileSystemMovedEvent):
        self.on_deleted(event)
        self.on_created(FileSystemEvent(event.dest_path))

    def on_deleted(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        # ignore non-mission files
        if not path.endswith('.miz'):
            return
        missions = self.server.settings['missionList']
        if '.dcssb' not in path:
            secondary = os.path.join(os.path.dirname(path), '.dcssb', os.path.basename(path))
            if secondary in missions:
                path = secondary
        if path in missions:
            idx = missions.index(path) + 1
            asyncio.run_coroutine_threadsafe(self.server.deleteMission(idx), self.loop)
            # cache the index of the line to re-add the file at the correct position afterward
            # if a cloud drive did a delete/add instead of a modification
            self.deleted[path] = idx
            self.log.info(f"=> Mission {os.path.basename(path)[:-4]} deleted from server {self.server.name}.")
        else:
            self.log.debug(f"Mission file {path} got deleted from disk.")


@dataclass
@DataObjectFactory.register()
class ServerImpl(Server):
    bot: Optional[DCSServerBot] = field(compare=False, init=False)
    event_handler: MissionFileSystemEventHandler = field(compare=False, default=None)
    observer: ObserverType = field(compare=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        self.is_remote = False
        self._lock = asyncio.Lock()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("INSERT INTO servers (server_name) VALUES (%s) ON CONFLICT (server_name) DO NOTHING",
                             (self.name, ))
            row = conn.execute("SELECT maintenance FROM servers WHERE server_name = %s", (self.name,)).fetchone()
            if row:
                self._maintenance = row[0]
        atexit.register(self.stop_observer)

    def __eq__(self, other):
        if isinstance(other, ServerImpl):
            return self.name == other.name
        return False

    def __hash__(self):
        return hash(self.name)

    async def reload(self):
        self.locals = self.read_locals()
        self._channels.clear()
        self._options = None
        self._settings = None
        self.prepare()

    async def get_missions_dir(self) -> str:
        return self.instance.missions_dir

    @property
    def settings(self) -> dict:
        if not self._settings:
            path = os.path.join(self.instance.home, 'Config', 'serverSettings.lua')
            self._settings = utils.SettingsDict(self, path, 'cfg')
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
            # make sure the most important settings are there
            self._options.setdefault("graphics", {}).update({"visibRange": "High"})
            self._options.setdefault("plugins", {})
            self._options.setdefault("difficulty", {})
            self._options.setdefault("miscellaneous", {})
        return self._options

    def set_instance(self, instance: Instance):
        self._instance = instance
        self.locals |= self.instance.locals
        self.prepare()

    def start_observer(self):
        if not self.observer:
            self.event_handler = MissionFileSystemEventHandler(self, asyncio.get_event_loop())
            self.observer = Observer()
            self.enable_autoscan()
            self.observer.start()

    def stop_observer(self):
        if self.observer:
            self.disable_autoscan()
            self.observer.stop()
            self.observer.join(timeout=10)
            self.observer = None

    def enable_autoscan(self):
        if not self.observer.emitters:
            self.observer.schedule(self.event_handler, self.instance.missions_dir, recursive=True)
            self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder enabled.')

    def disable_autoscan(self):
        if self.observer.emitters:
            self.observer.unschedule_all()
            self.log.info(f'  => {self.name}: Auto-scanning for new miz files in Missions-folder disabled.')

    def _init_mission_list(self):
        # make sure all missions in the directory are in the mission list ...
        directory = Path(self.instance.missions_dir)
        missions = self.settings['missionList']
        i: int = 0
        for file in directory.rglob('*.miz'):
            if '.dcssb' in str(file):
                continue
            secondary = os.path.join(os.path.dirname(file), '.dcssb', os.path.basename(file))
            if str(file) not in missions and secondary not in missions:
                missions.append(str(file))
                i += 1
        # make sure the list is written to serverSettings.lua
        self.settings['missionList'] = missions
        if i:
            self.log.info(f"  => {self.name}: {i} missions auto-added to the mission list")

    def _make_missions_unique(self):
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

    async def _load_mission_list(self):
        try:
            data = await self.send_to_dcs_sync({"command": "listMissions"}, timeout=60)
            mission_list = data['missionList']
            if mission_list != self.settings['missionList']:
                for m in set(self.settings['missionList']) - set(mission_list):
                    self.log.warning(f"Removed non-existing/unsupported mission from the list: {m}")
                self.settings['missionList'] = mission_list
        except (TimeoutError, asyncio.TimeoutError):
            pass

    def set_status(self, status: Union[Status, str]):
        if isinstance(status, str):
            new_status = Status(status)
        else:
            new_status = status
        if new_status != self._status:
            # make sure the mission list is tidy on the first start
            if self._status == Status.UNREGISTERED and status == Status.SHUTDOWN:
                if self.locals.get('autoscan', False):
                    self._init_mission_list()
                else:
                    self._make_missions_unique()
                super().set_status(status)
            elif self._status in [Status.UNREGISTERED, Status.LOADING] and new_status in [Status.RUNNING, Status.PAUSED]:
                # only check the mission list if we started that server
                if self._status == Status.LOADING:
                    if self.locals.get('validate_missions', True):
                        asyncio.create_task(self._load_mission_list())
                asyncio.create_task(self.init_extensions())
                asyncio.create_task(self._startup_extensions(status))
            elif self._status in [Status.RUNNING, Status.PAUSED, Status.SHUTTING_DOWN] and new_status in [Status.STOPPED, Status.SHUTDOWN]:
                asyncio.create_task(self._shutdown_extensions(status))
            else:
                super().set_status(status)

    async def update_channels(self, channels: dict[str, int]) -> None:
        config_file = os.path.join(self.node.config_dir, 'servers.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        config[self.name]['channels'] = channels
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)
        self.locals.setdefault('channels', {}).update(channels)
        self._channels.clear()

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
                admin_channel = data.get('channels', {}).get('admin', -1)
            with open(os.path.join('Scripts', 'net', 'DCSServerBot', 'DCSServerBotConfig.lua.tmpl'), mode='r',
                      encoding='utf-8') as template:
                with open(os.path.join(bot_home, 'DCSServerBotConfig.lua'), mode='w', encoding='utf-8') as outfile:
                    for line in template.readlines():
                        line = utils.format_string(line, node=self.node, instance=self.instance, server=self,
                                                   admin_channel=admin_channel)
                        outfile.write(line)
        except KeyError as k:
            self.log.error(f'! You must set a value for {k}. See README for help.')
            raise k
        except Exception as ex:
            self.log.exception(ex)
        self.log.debug(f"  - Installing Plugin luas into {self.instance.name} ...")
        for plugin_name in self.node.plugins:
            self._install_plugin(plugin_name)
        self.log.debug(f'  - Luas installed into {self.instance.name}.')

    def prepare(self):
        if self.settings.get('name', 'DCS Server') != self.name:
            self.settings['name'] = self.name
        # enable persistence
        if not self.settings.get('advanced'):
            self.settings['advanced'] = {}
        if not self.settings['advanced'].get('sav_autosave', False):
            self.settings['advanced']['sav_autosave'] = True
        if 'serverSettings' in self.locals:
            for key, value in self.locals['serverSettings'].items():
                if key == 'advanced':
                    self.settings['advanced'].update(value)
                else:
                    self.settings[key] = value
        self._install_luas()
        # enable autoscan for missions changes
        if self.locals.get('autoscan', False):
            self.start_observer()

    def _get_current_mission_file(self) -> Optional[str]:
        if not self.current_mission or not self.current_mission.filename:
            settings = self.settings
            try:
                start_index = int(settings.get('listStartIndex', 1))
            except ValueError:
                start_index = settings['listStartIndex'] = 1
            if settings['missionList'] and start_index <= len(settings['missionList']):
                filename = settings['missionList'][start_index - 1]
            else:
                filename = None
            if not filename or not os.path.exists(filename):
                for idx, filename in enumerate(settings['missionList']):
                    if os.path.exists(filename):
                        settings['listStartIndex'] = idx + 1
                        break
                    else:
                        self.log.warning(f"Non-existent mission {filename} in your missionList!")
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
            miz = await asyncio.to_thread(MizFile, filename)
            return miz.theatre
        return None

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

    async def send_to_dcs(self, message: dict):
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
                # proper rename
                if old_name in data and new_name not in data:
                    data[new_name] = data.pop(old_name)
                # new added server
                elif not old_name:
                    data[new_name] = {}
                with open(filename, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(data, outfile)
            # update serverSettings.lua if requested
            if update_settings:
                self.settings['name'] = new_name

        async def update_database(old_name: str, new_name: str):
            # rename the server in the database
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    # we need to remove any older server that might have had the same name
                    await conn.execute('DELETE FROM servers WHERE server_name = %s', (new_name, ))
                    await conn.execute("""
                        UPDATE servers SET server_name = %s WHERE server_name IS NOT DISTINCT FROM %s
                    """, (new_name, old_name))
                    await conn.execute('DELETE FROM instances WHERE server_name = %s', (new_name, ))
                    await conn.execute("""
                        UPDATE instances 
                        SET server_name = %s 
                        WHERE instance = %s AND server_name IS NOT DISTINCT FROM %s
                    """, (new_name, self.instance.name, old_name))
                    await conn.execute('DELETE FROM message_persistence WHERE server_name = %s', (new_name, ))
                    await conn.execute("""
                        UPDATE message_persistence 
                        SET server_name = %s 
                        WHERE server_name IS NOT DISTINCT FROM %s
                    """, (new_name, old_name))

        async def update_cluster(new_name: str):
            # only the master can take care of a cluster-wide rename
            if self.node.master:
                await self.node.rename_server(self, new_name)
            else:
                await self.bus.send_to_node_sync({
                    "command": "rpc",
                    "object": "Node",
                    "method": "rename_server",
                    "params": {
                        "server": self.name or 'n/a',
                        "new_name": new_name
                    }
                })
                self.bus.rename_server(self, new_name)

        old_name = self.name
        if old_name == 'n/a':
            old_name = None
        try:
            await update_database(old_name, new_name)
            await update_cluster(new_name)
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

    @performance_log()
    def do_startup(self):
        basepath = self.node.installation
        for exe in ['DCS_server.exe', 'DCS.exe']:
            path = os.path.join(basepath, 'bin', exe)
            if os.path.exists(path):
                break
        else:
            raise FileNotFoundError(f"No executable found to start a DCS server in {basepath}!")

        # check if all missions are existing
        missions = []
        try:
            start_mission = self.settings['missionList'][int(self.settings.get('listStartIndex', 1)) - 1]
        except IndexError:
            start_mission = None
        for mission in self.settings['missionList']:
            if '.dcssb' in mission:
                _mission = os.path.join(os.path.dirname(os.path.dirname(mission)), os.path.basename(mission))
            else:
                _mission = mission
            # check if the orig file has been updated
            orig = utils.get_orig_file(_mission, create_file=False)
            if orig and os.path.exists(orig) and os.path.exists(mission) and os.path.getmtime(orig) > os.path.getmtime(mission):
                shutil.copy2(orig, _mission)
                missions.append(_mission)
            elif os.path.exists(mission):
                missions.append(mission)
            else:
                self.log.warning(f"Removing mission {mission} from serverSettings.lua as it could not be found!")
        if len(missions) != len(self.settings['missionList']):
            self.settings['missionList'] = missions
            if start_mission:
                try:
                    idx = missions.index(start_mission) + 1
                except ValueError:
                    idx = 1
                self.settings['listStartIndex'] = idx
            self.log.warning('Removed non-existent missions from serverSettings.lua')
        self.log.debug(r'Launching DCS server with: "{}" --server --norender -w {}'.format(path, self.instance.name))
        try:
            p = subprocess.Popen(
                [exe, '--server', '--norender', '-w', self.instance.name], executable=path, close_fds=True
            )
            self.process = psutil.Process(p.pid)
            if 'priority' in self.locals:
                self.set_priority(self.locals.get('priority'))
            if 'affinity' in self.locals:
                self.set_affinity(self.locals.get('affinity'))
            else:
                # make sure, we only use P-cores for DCS servers
                e_core_affinity = utils.get_e_core_affinity()
                if e_core_affinity:
                    self.log.info(f"  => P/E-Core CPU detected.")
                    self.set_affinity(utils.get_cpus_from_affinity(utils.get_p_core_affinity()))
            self.log.info(f"  => DCS server starting up with PID {p.pid}")
        except Exception:
            self.log.error(f"  => Error while trying to launch DCS!", exc_info=True)
            self.process = None

    def load_extension(self, name: str) -> Optional[Extension]:
        if '.' not in name:
            _extension = f'extensions.{name.lower()}.extension.{name}'
        else:
            _extension = name
        _ext = utils.str_to_class(_extension)
        if not _ext:
            self.log.error(f"Extension {name} could not be found!")
            return None
        return _ext(
            self,
            self.node.locals.get('extensions', {}).get(name, {}) | (DEFAULT_EXTENSIONS | self.locals.get('extensions', {}))[name]
        )

    async def init_extensions(self) -> list[str]:
        async with self._lock:
            extensions = DEFAULT_EXTENSIONS | self.locals.get('extensions', {})
            for extension in extensions.keys():
                try:
                    ext: Extension = self.extensions.get(extension)
                    if not ext:
                        ext = self.load_extension(extension)
                        if not ext:
                            continue
                        if ext.is_installed():
                            self.extensions[extension] = ext
                except InstallException as ex:
                    self.log.error(f"  => Error while loading extension {extension}: {ex} - skipped")
                except Exception as ex:
                    self.log.exception(ex)
            return list(self.extensions.keys())

    async def prepare_extensions(self):
        async with self._lock:
            for ext in self.extensions.values():
                try:
                    await ext.prepare()
                except InstallException as ex:
                    self.log.error(f"  => Error during {ext.name}.prepare(): {ex} - skipped")
                except Exception:
                    self.log.error(f"  => Unknown error during {ext.name}.prepare() - skipped.", exc_info=True)

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

    def set_priority(self, priority: str):
        if priority == 'below_normal':
            self.log.info("  => Setting process priority to BELOW NORMAL.")
            p = psutil.BELOW_NORMAL_PRIORITY_CLASS
        elif priority == 'above_normal':
            self.log.info("  => Setting process priority to ABOVE NORMAL.")
            p = psutil.ABOVE_NORMAL_PRIORITY_CLASS
        elif priority == 'high':
            self.log.info("  => Setting process priority to HIGH.")
            p = psutil.HIGH_PRIORITY_CLASS
        elif priority == 'realtime':
            self.log.warning("  => Setting process priority to REALTIME. Handle with care!")
            p = psutil.REALTIME_PRIORITY_CLASS
        else:
            p = psutil.NORMAL_PRIORITY_CLASS
        self.process.nice(p)

    def set_affinity(self, affinity: Union[list[int], str]):
        if isinstance(affinity, str):
            affinity = [int(x.strip()) for x in affinity.split(',')]
        elif isinstance(affinity, int):
            affinity = [affinity]
        self.log.info("  => Setting process affinity to {}".format(','.join(map(str, affinity))))
        self.process.cpu_affinity(affinity)

    async def startup(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = True) -> None:
        if not utils.is_desanitized(self.node):
            if not self.node.locals['DCS'].get('desanitize', True):
                raise Exception("Your DCS installation is not desanitized properly to be used with DCSServerBot!")
            else:
                utils.desanitize(self)
        else:
            self.log.debug("MissionScripting.lua is already desanitized.")
        self.status = Status.LOADING
        await self.init_extensions()
        await self.prepare_extensions()
        if modify_mission:
            await self.apply_mission_changes(use_orig=use_orig)
        await asyncio.to_thread(self.do_startup)
        timeout = 300 if self.node.locals.get('slow_system', False) else 180
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

    async def _startup_extensions(self, status: Union[Status, str]) -> None:
        async with self._lock:
            not_running_extensions = [
                ext for ext in self.extensions.values() if not await asyncio.to_thread(ext.is_running)
            ]
            startup_coroutines = [ext.startup() for ext in not_running_extensions]

            results = await asyncio.gather(*startup_coroutines, return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    tb_str = "".join(
                        traceback.format_exception(type(res), res, res.__traceback__))
                    self.log.error(f"Error during startup_extension(): %s", tb_str)
            # set the status after the extensions have been started
            super().set_status(status)

    async def _shutdown_extensions(self, status: Union[Status, str]) -> None:
        async with self._lock:
            running_extensions = [
                ext for ext in self.extensions.values() if await asyncio.to_thread(ext.is_running)
            ]
            shutdown_coroutines = [asyncio.to_thread(ext.shutdown) for ext in running_extensions]

            results = await asyncio.gather(*shutdown_coroutines, return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    self.log.error(f"Error during shutdown_extension()", exc_info=res)

            # set the status after the extensions have been shut down
            super().set_status(status)

    async def do_shutdown(self):
        self.status = Status.SHUTTING_DOWN
        slow_system = self.node.locals.get('slow_system', False)
        timeout = 300 if slow_system else 180
        await self.send_to_dcs({"command": "shutdown"})
        with suppress(TimeoutError, asyncio.TimeoutError):
            await self.wait_for_status_change([Status.STOPPED, Status.SHUTDOWN], timeout)
        self.current_mission = None

    async def shutdown(self, force: bool = False) -> None:
        if await self.is_running():
            if not force:
                await self.do_shutdown()
                # wait 30/60s for the process to terminate
                for i in range(1, 60 if self.node.locals.get('slow_system', False) else 30):
                    if not self.process or not self.process.is_running():
                        break
                    await asyncio.sleep(1)
            await self._terminate()
        self.status = Status.SHUTDOWN
        logfile = os.path.join(self.instance.home, 'Logs', 'dcs.log')
        if os.path.exists(logfile):
            shutil.copy2(logfile, os.path.join(self.instance.home, 'Logs',
                                               f"dcs-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"))

    async def is_running(self) -> bool:
        async with self._lock:
            if not self.process or not self.process.is_running():
                self.process = await asyncio.to_thread(
                    lambda: next(utils.find_process("DCS_server.exe|DCS.exe", self.instance.name), None)
                )
            return self.process is not None

    async def _terminate(self) -> None:
        try:
            if not self.process or not self.process.is_running():
                return
            self.process.terminate()
            # wait 30/60s for the process to terminate
            for i in range(1, 60 if self.node.locals.get('slow_system', False) else 30):
                if not self.process or not self.process.is_running():
                    return
                await asyncio.sleep(1)
            else:
                self.process.kill()
        except psutil.NoSuchProcess:
            pass
        finally:
            self.process = None

    @performance_log()
    async def stop(self) -> None:
        async def wait_for_file_release(timeout: int):
            mission_file = self._get_current_mission_file()
            if not mission_file:
                return
            for i in range(0, timeout * 2):
                try:
                    with open(mission_file, mode='a'):
                        return
                except PermissionError:
                    await asyncio.sleep(0.5)
            else:
                raise TimeoutError()

        if self.status in [Status.PAUSED, Status.RUNNING]:
            timeout = 120 if self.node.locals.get('slow_system', False) else 60
            await self.send_to_dcs({"command": "stop_server"})
            await self.wait_for_status_change([Status.STOPPED], timeout)
            await wait_for_file_release(10)

    @performance_log()
    async def apply_mission_changes(self, filename: Optional[str] = None, *, use_orig: Optional[bool] = True) -> str:
        try:
            # disable autoscan
            if self.locals.get('autoscan', False):
                self.disable_autoscan()
            if not filename:
                filename = await self.get_current_mission_file()
                if not filename:
                    self.log.warning("No mission found. Is your mission list empty?")
                    return filename

            # create a writable mission
            new_filename = utils.create_writable_mission(filename)
            if use_orig:
                # get the orig file
                orig_filename = utils.get_orig_file(new_filename)
                # and copy the orig file over
                shutil.copy2(orig_filename, new_filename)
            elif new_filename != filename:
                shutil.copy2(filename, new_filename)
            try:
                # process all mission modifications
                dirty = False
                for ext in self.extensions.values():
                    if type(ext).beforeMissionLoad != Extension.beforeMissionLoad:
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
                    try:
                        index = missions.index(filename) + 1
                        await self.replaceMission(index, new_filename)
                    except ValueError:
                        # we should not be here, but just in case
                        if new_filename not in missions:
                            await self.addMission(new_filename)
                return new_filename
            except Exception as ex:
                self.log.error(ex)
                if filename != new_filename and os.path.exists(new_filename):
                    os.remove(new_filename)
                return filename
        finally:
            # enable autoscan
            if self.locals.get('autoscan', False):
                self.enable_autoscan()

    async def keep_alive(self):
        if self.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await self.send_to_dcs({"command": "getMissionUpdate"})
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE instances SET last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE node = %s AND server_name = %s
                """, (self.node.name, self.name))

    async def uploadMission(self, filename: str, url: str, *, missions_dir: str = None, force: bool = False,
                            orig = False) -> UploadStatus:
        if not missions_dir:
            missions_dir = self.instance.missions_dir
        filename = os.path.normpath(os.path.join(missions_dir, filename))
        secondary = os.path.join(os.path.dirname(filename), '.dcssb', os.path.basename(filename))
        if orig:
            filename = secondary + '.orig'
            add = False
        else:
            for idx, name in enumerate(self.settings['missionList']):
                if (os.path.normpath(name) == filename) or (os.path.normpath(name) == secondary):
                    if self.current_mission and idx == int(self.settings['listStartIndex']) - 1:
                        if not force:
                            return UploadStatus.FILE_IN_USE
                    add = True
                    break
            else:
                add = self.locals.get('autoadd', True)
        rc = await self.node.write_file(filename, url, force)
        if rc != UploadStatus.OK:
            return rc
        if (force or not self.locals.get('autoscan', False)) and add:
            await self.addMission(filename)
        return UploadStatus.OK

    async def modifyMission(self, filename: str, preset: Union[list, dict], use_orig: bool = True) -> str:
        from extensions.mizedit import MizEdit

        # create a writable mission
        new_filename = utils.create_writable_mission(filename)
        if use_orig:
            # get the orig file
            orig_filename = utils.get_orig_file(new_filename)
            # and copy the orig file over
            shutil.copy2(orig_filename, new_filename)
        elif new_filename != filename:
            shutil.copy2(filename, new_filename)
        await MizEdit.apply_presets(self, new_filename, preset)
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

    async def restart(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = True) -> None:
        await self.loadMission(int(self.settings['listStartIndex']), modify_mission=modify_mission, use_orig=use_orig)

    async def setStartIndex(self, mission_id: int) -> None:
        if mission_id > len(self.settings['missionList']):
            mission_id = 1
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            await self.send_to_dcs({"command": "setStartIndex", "id": mission_id})
        else:
            self.settings['listStartIndex'] = mission_id

    async def setPassword(self, password: str):
        self.settings['password'] = password or ''

    async def setCoalitionPassword(self, coalition: Coalition, password: str):
        advanced = self.settings['advanced']
        if coalition == Coalition.BLUE:
            if password:
                advanced['bluePasswordHash'] = utils.hash_password(password)
            else:
                advanced.pop('bluePasswordHash', None)
        else:
            if password:
                advanced['redPasswordHash'] = utils.hash_password(password)
            else:
                advanced.pop('redPasswordHash', None)
        self.settings['advanced'] = advanced
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute('UPDATE servers SET {} = %s WHERE server_name = %s'.format(
                    'blue_password' if coalition == Coalition.BLUE else 'red_password'),
                    (password, self.name))

    async def addMission(self, path: str, *, idx: Optional[int] = -1, autostart: Optional[bool] = False) -> list[str]:
        path = os.path.normpath(path)
        secondary = os.path.join(os.path.dirname(path), '.dcssb', os.path.basename(path))
        orig = secondary + '.orig'
        if os.path.exists(orig):
            os.remove(orig)
        missions = self.settings['missionList']
        if path in missions or secondary in missions:
            # the mission is already in the list. check if we need to reset a .dcssb copy
            if secondary in missions:
                await self.replaceMission(missions.index(secondary) + 1, path)
                with suppress(Exception):
                    os.remove(secondary)
            return missions
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            data = await self.send_to_dcs_sync({
                "command": "addMission",
                "path": path,
                "index": idx,
                "autostart": autostart
            })
            self.settings['missionList'] = data['missionList']
        else:
            if idx > 0:
                missions.insert(idx - 1, path)
            else:
                missions.append(path)
            self.settings['missionList'] = missions
            if autostart:
                self.settings['listStartIndex'] = missions.index(path if path in missions else secondary) + 1
        return self.settings['missionList']

    async def deleteMission(self, mission_id: int) -> list[str]:
        if self.status in [Status.PAUSED, Status.RUNNING] and self.mission_id == mission_id:
            raise AttributeError("Can't delete the running mission!")
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            data = await self.send_to_dcs_sync({"command": "deleteMission", "id": mission_id})
            self.settings['missionList'] = data['missionList']
        else:
            missions = self.settings['missionList']
            del missions[mission_id - 1]
            self.settings['missionList'] = missions
        return self.settings['missionList']

    async def replaceMission(self, mission_id: int, path: str) -> list[str]:
        path = os.path.normpath(path)
        if self.status in [Status.STOPPED, Status.PAUSED, Status.RUNNING]:
            await self.send_to_dcs_sync({"command": "replaceMission", "index": mission_id, "path": path})
        else:
            missions: list[str] = self.settings['missionList']
            missions[mission_id - 1] = path
            self.settings['missionList'] = missions
        return self.settings['missionList']

    async def loadMission(self, mission: Union[int, str], modify_mission: Optional[bool] = True,
                          use_orig: Optional[bool] = True, no_reload: Optional[bool] = False) -> Optional[bool]:
        start_index = int(self.settings.get('listStartIndex', 1))
        mission_list = self.settings['missionList']
        # check if we re-load the running mission
        if ((isinstance(mission, int) and mission == start_index) or
            (isinstance(mission, str) and mission == self._get_current_mission_file())):
            # if we should not reload, then return here
            if no_reload:
                return None
            mission = self._get_current_mission_file()
            if not mission:
                return False
            if use_orig:
                new_filename = utils.create_writable_mission(mission)
                orig_mission = utils.get_orig_file(mission)
                shutil.copy2(orig_mission, new_filename)
                if new_filename != mission:
                    mission_list = await self.replaceMission(start_index, new_filename)
            elif modify_mission:
                # don't use the orig file, still make sure we have a writable mission
                new_filename = utils.create_writable_mission(mission)
                if new_filename != mission:
                    shutil.copy2(mission, new_filename)
                    mission_list = await self.replaceMission(start_index, new_filename)

        if isinstance(mission, int):
            if mission > len(mission_list):
                mission = 1
            filename = mission_list[mission - 1]
        else:
            filename = mission
        if modify_mission:
            filename = await self.apply_mission_changes(filename)

        if self.status == Status.STOPPED:
            try:
                idx = mission_list.index(filename) + 1
                self.settings['listStartIndex'] = idx
                self.settings['current'] = idx
                return await self.start()
            except ValueError:
                return False
        else:
            try:
                idx = mission_list.index(filename) + 1
                if idx == start_index:
                    rc = await self.send_to_dcs_sync({"command": "startMission", "filename": filename})
                else:
                    rc = await self.send_to_dcs_sync({"command": "startMission", "id": idx})
            except ValueError:
                rc = await self.send_to_dcs_sync({"command": "startMission", "filename": filename})
            # We could not load the mission
            result = rc['result'] if isinstance(rc['result'], bool) else (rc['result'] == 0)
            if not result:
                return False
            # wait for a status change (STOPPED or LOADING)
            await self.wait_for_status_change([Status.STOPPED, Status.LOADING], timeout=120)
            # wait until we are running again
            await self.wait_for_status_change([Status.RUNNING, Status.PAUSED], timeout=300)
        return True

    async def loadNextMission(self, modify_mission: Optional[bool] = True, use_orig: Optional[bool] = False) -> bool:
        init_mission_id = int(self.settings['listStartIndex'])
        max_mission_id = len(self.settings['missionList'])
        mission_id = init_mission_id + 1
        if mission_id > max_mission_id:
            mission_id = 1
        while not await self.loadMission(mission_id, modify_mission, use_orig):
            mission_id += 1
            if mission_id > max_mission_id:
                mission_id = 1
            if mission_id == init_mission_id:
                break
        else:
            return True
        return False

    async def getMissionList(self) -> list[str]:
        return self.settings.get('missionList', [])

    async def run_on_extension(self, extension: str, method: str, **kwargs) -> Any:
        ext = self.extensions.get(extension)
        if not ext:
            raise ValueError(f"Extension {extension} not found.")
        # Check if the command exists in the extension object
        if not hasattr(ext, method):
            raise ValueError(f"Command {method} not found in extension {extension}.")

        # Access the method
        _method = getattr(ext, method)

        # Check if it is a coroutine
        if asyncio.iscoroutinefunction(_method):
            result = await _method(**kwargs)
        else:
            result = await asyncio.to_thread(_method, **kwargs)
        return result

    async def config_extension(self, name: str, config: dict) -> None:
        config_file = os.path.join(self.node.config_dir, 'nodes.yaml')
        data: dict = yaml.load(Path(config_file).read_text(encoding='utf-8'))
        node_config = data.get(self.node.name, {})
        extensions = node_config.setdefault('instances', {}).setdefault(
            self.instance.name, {}
        ).setdefault('extensions', {})
        extensions[name] = extensions.get(name, {}) | config
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
        # re-read config
        self.node.locals = self.node.read_locals()
        self.locals |= self.node.locals['instances'][self.instance.name]
        self.instance.locals |= self.node.locals['instances'][self.instance.name]
        if name in self.extensions:
            self.extensions[name].config = self.node.locals.get('extensions', {}).get(name, {}) | self.locals['extensions'][name]

    async def install_extension(self, name: str, config: dict) -> None:
        if name in self.extensions:
            raise InstallException(f"Extension {name} is already installed!")
        await self.config_extension(name, config)
        ext = self.load_extension(name)
        await ext.install()
        self.extensions[name] = ext

    async def uninstall_extension(self, name: str) -> None:
        ext = self.extensions[name]
        if not ext:
            raise UninstallException(f"Extension {name} is not installed!")
        await ext.uninstall()
        self.extensions.pop(name, None)
        await self.config_extension(name, {"enabled": False})

    async def cleanup(self) -> None:
        tempdir = os.path.join(tempfile.gettempdir(), self.instance.name)
        await asyncio.to_thread(utils.safe_rmtree, tempdir)

    async def getAllMissionFiles(self) -> list[tuple[str, str]]:
        def shorten_filename(file: str) -> str:
            if file.endswith('.orig'):
                return file[:-5]
            if '.dcssb' in file:
                return os.path.join(os.path.dirname(file).replace('.dcssb', ''), os.path.basename(file))
            return file

        result = []
        base_dir, all_missions = await self.node.list_directory(self.instance.missions_dir, pattern="*.miz",
                                                                ignore=['.dcssb', 'Scripts', 'Saves'], traverse=True)
        for mission in all_missions:
            orig = utils.get_orig_file(mission, create_file=False)
            secondary = os.path.join(
                os.path.dirname(mission), '.dcssb', os.path.basename(mission)
            )
            if orig and os.path.getmtime(orig) > os.path.getmtime(mission):
                file = orig
            else:
                file = mission
            if os.path.exists(secondary) and os.path.getmtime(secondary) > os.path.getmtime(file):
                file = secondary

            result.append((shorten_filename(file), file))

        return result

    def _install_plugin(self, plugin: str) -> None:
        source_path = f'./plugins/{plugin}/lua'
        if os.path.exists(source_path):
            target_path = os.path.join(self.instance.home, 'Scripts', 'net', 'DCSServerBot', plugin)
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
            self.log.debug(f'    => Plugin {plugin.capitalize()} installed.')

    async def install_plugin(self, plugin: str) -> None:
        self._install_plugin(plugin)

    async def uninstall_plugin(self, plugin: str) -> None:
        target_path = os.path.join(self.instance.home, 'Scripts', 'net', 'DCSServerBot', plugin)
        if os.path.exists(target_path):
            utils.safe_rmtree(target_path)
            self.log.debug(f'    => Plugin {plugin.capitalize()} uninstalled.')
