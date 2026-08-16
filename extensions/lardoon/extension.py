import aiohttp
import asyncio
import atexit
import certifi
import os
import psutil
import ssl
import subprocess

from contextlib import suppress
from core import Server, utils, get_translation, PortType, Port, ThreadSafeDict, ProcessManager, InstallableExtension, \
    ServerMaintenanceManager
from discord.ext import tasks
from threading import Thread
from typing_extensions import override

from extensions.tacview import TACVIEW_DEFAULT_DIR

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "Lardoon"
]

LARDOON_GITHUB_URL = "https://api.github.com/repos/Special-K-s-Flightsim-Bots/lardoon/releases/latest"
LARDOON_DOWNLOAD_URL = "https://github.com/Special-K-s-Flightsim-Bots/lardoon/releases/download/v{version}"


class Lardoon(InstallableExtension):
    _process: psutil.Process | None = None
    _servers: set[str] = set()
    _tacview_dirs: dict[str, set[str]] = ThreadSafeDict()
    _lock = asyncio.Lock()

    NODE_CONFIG_DICT = {
        "cmd": {
            "type": str,
            "label": _("Command"),
            "placeholder": "Path to Lardoon executable",
            "required": True
        },
        "bind": {
            "type": str,
            "label": _("Bind Address"),
            "placeholder": "ip:port",
            "default": "0.0.0.0:3113"
        },
        "url": {
            "type": str,
            "label": _("URL"),
            "required": False
        },
        "minutes": {
            "type": int,
            "label": _("Scan (min)"),
            "default": 5,
            "required": False
        },
        "use_single_process": {
            "type": bool,
            "label": _("Use a single lardoon process"),
            "default": True
        }
    }

    CONFIG_DICT = {
        "bind": {
            "type": str,
            "label": _("Bind Address"),
            "placeholder": "ip:port",
            "required": False
        },
        "url": {
            "type": str,
            "label": _("URL"),
            "required": False
        },
        "minutes": {
            "type": int,
            "label": _("Scan (min)"),
            "default": 5,
            "required": False
        },
        "debug": {
            "type": bool,
            "label": _("Debug"),
            "default": False
        }
    }

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        if self.config.get('use_single_process', True):
            type(self)._process = self.process = self.find_running_process(type(self)._process)
        else:
            self.process = self.find_running_process(None)

    def get_inst_path(self) -> str:
        return os.path.dirname(os.path.expandvars(self.config['cmd'])) if 'cmd' in self.config else ''

    def get_exe_path(self) -> str | None:
        return os.path.expandvars(self.config['cmd']) if 'cmd' in self.config else None

    def find_running_process(self, p: psutil.Process | None = None):
        if not p or not p.is_running():
            cmd = self.get_exe_path()
            if not cmd:
                return None
            p = next(utils.find_process(os.path.basename(cmd), self.config['bind']), None)
            if p:
                if self.config.get('use_single_process', True):
                    instance = None
                else:
                    instance = self.server.instance.name
                ProcessManager().assign_process(
                    p,
                    min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
                    max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
                    quality=self.config.get('auto_affinity', {}).get('quality', 1),
                    instance=instance)
                self.log.debug("- Running Lardoon process found.")
            return p
        else:
            return p

    def _get_tacview_dir(self) -> str:
        return os.path.expandvars(
            self.config.get('tacviewExportPath', self.server.options['plugins']['Tacview'].get(
                'tacviewExportPath')) or TACVIEW_DEFAULT_DIR
        )

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False

        async with type(self)._lock:
            if self.config.get('use_single_process', True):
                self.process = type(self)._process

            if not self.process or not self.process.is_running():
                def log_stream(proc: psutil.Popen, stream: str):
                    pipe = proc.stdout if stream == 'stdout' else proc.stderr
                    for line in iter(pipe.readline, b''):
                        if stream == 'stdout':
                            self.log.debug(line.decode('utf-8').rstrip())
                        else:
                            self.log.error(line.decode('utf-8').rstrip())

                def run_subprocess():
                    if self.config.get('use_single_process', True):
                        cwd = None
                        instance = None
                    else:
                        instance = self.server.instance.name
                        cwd = os.path.join(self.server.instance.home, 'Config')
                    out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
                    cmd = self.get_exe_path()
                    self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
                    proc = ProcessManager().launch_process(
                        [cmd, "serve", "--bind", self.config['bind']],
                        executable=os.path.expandvars(self.config['cmd']),
                        cwd=cwd,
                        min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
                        max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
                        quality=self.config.get('auto_affinity', {}).get('quality', 1),
                        instance=instance,
                        stdout=out, stderr=subprocess.PIPE
                    )
                    if self.config.get('debug', False):
                        Thread(target=log_stream, args=(proc, 'stdout'), daemon=True).start()
                    Thread(target=log_stream, args=(proc, 'stderr'), daemon=True).start()
                    return proc

                try:
                    self.process = await asyncio.to_thread(run_subprocess)
                    atexit.register(self.terminate)
                except psutil.NoSuchProcess:
                    self.log.error(f"Error during launch of {self.get_exe_path()}!")
                    return False

        if self.config.get('use_single_process', True):
            type(self)._process = self.process
            type(self)._servers.add(self.server.name)
            tacview_dir = self._get_tacview_dir()
            if tacview_dir not in type(self)._tacview_dirs:
                type(self)._tacview_dirs[tacview_dir] = set()
            type(self)._tacview_dirs[tacview_dir].add(self.server.name)
        else:
            utils.safe_start(self._schedule)
        return await super().startup()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            if self.config.get('use_single_process', True):
                type(self)._process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.get_exe_path()}: {str(ex)}")
            return False

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        super().shutdown()
        if self.config.get('use_single_process', True):
            if self.server.name in type(self)._servers:
                type(self)._servers.remove(self.server.name)
            tacview_dir = self._get_tacview_dir()
            type(self)._tacview_dirs[tacview_dir].discard(self.server.name)
            if not type(self)._servers:
                if self.terminate():
                    return True
                return False
            return True
        else:
            # we do not wait here due to not being async
            self._schedule.cancel()
            return self.terminate()

    @override
    def is_running(self) -> bool:
        if self.config.get('use_single_process', True):
            return type(self)._process and type(self)._process.is_running() and self.server.name in type(self)._servers
        else:
            return self.process is not None and self.process.is_running()

    @override
    @property
    def version(self) -> str | None:
        return utils.get_windows_version(self.get_exe_path())

    @override
    def is_available(self) -> bool:
        exe = self.get_exe_path()
        return exe is not None and os.path.exists(exe)

    @override
    async def render(self, param: dict | None = None) -> dict:
        ret = await super().render(param)
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return ret | {
            "value": value
        }

    @tasks.loop(minutes=1.0)
    async def _schedule(self):
        def run_subprocess(args):
            if self.config.get('use_single_process', True):
                cwd = None
            else:
                cwd = os.path.join(self.server.instance.home, 'Config')

            proc = subprocess.Popen([cmd] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            if stderr:
                self.log.debug(f"{self.name}: {stderr.decode('utf-8')}")
            if self.config.get('debug', False) and stdout:
                self.log.debug(f"{self.name}: {stdout.decode('utf-8')}")

        # make sure we're running on the correct schedule
        minutes = self.config.get('minutes', 5)
        if self._schedule.minutes != minutes:
            self._schedule.change_interval(minutes=minutes)

        cmd = self.get_exe_path()
        if self.config.get('use_single_process', True):
            for tacview_dir, server_list in type(self)._tacview_dirs.items():
                if not server_list:
                    continue
                try:
                    async with type(self)._lock:
                        self.log.debug("Lardoon: Scheduled import run ...")
                        await asyncio.to_thread(run_subprocess, ["import", "-p", tacview_dir])
                    async with type(self)._lock:
                        self.log.debug("Lardoon: Scheduled prune run ...")
                        await asyncio.to_thread(run_subprocess, ["prune", "--no-dry-run"])
                except Exception as ex:
                    self.log.exception(ex)
        else:
            await asyncio.to_thread(run_subprocess, ["import", "-p", self._get_tacview_dir()])
            await asyncio.to_thread(run_subprocess, ["prune", "--no-dry-run"])

    @tasks.loop(count=1)
    async def schedule(self):
        if self.config.get('master_only', False) and not self.node.master:
            return
        if self.config.get('use_single_process', True):
            utils.safe_start(self._schedule)
        return

    @override
    def get_ports(self) -> dict[str, Port]:
        return {
            "Lardoon": Port(self.config['bind'].split(':')[1], PortType.TCP, public=True)
        } if self.enabled else {}

    @override
    async def get_latest_version(self) -> str | None:
        with suppress(aiohttp.ClientError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(
                        LARDOON_GITHUB_URL,
                        proxy=self.node.proxy,
                        proxy_auth=self.node.proxy_auth,
                        raise_for_status=True
                ) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        data = data[0]
                    return data.get('tag_name', '').strip('v')
        return None

    @override
    def is_installed(self) -> bool:
        return self.is_available()

    async def do_install(self, version: str) -> bool:
        def stop_processes():
            for process in utils.find_process(os.path.basename(self.get_exe_path())):
                if process and process.is_running():
                    process.terminate()

        async with ServerMaintenanceManager(self.node, shutdown=False):
            # stop any existing Lardoon process
            if self.is_installed():
                await asyncio.to_thread(stop_processes)

            download_url = LARDOON_DOWNLOAD_URL.format(version=version) + '/lardoon.exe'
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        download_url,
                        proxy=self.node.proxy,
                        proxy_auth=self.node.proxy_auth,
                        raise_for_status=True
                ) as response:
                    os.makedirs(self.get_inst_path(), exist_ok=True)
                    with open(self.get_exe_path(), 'wb') as f:
                        f.write(await response.content.read())

        return True

    @override
    async def install(self, version: str | None = None) -> bool:
        if self.is_installed():
            return True
        try:
            if not version:
                version = await self.get_latest_version()
            if await self.do_install(version):
                self.log.info(f"{self.name} version {version} installed.")
                return True
            return False
        except Exception as ex:
            self.log.error(f"Failed to install {self.name}: {ex}")
            return False

    @override
    async def uninstall(self) -> bool:
        # we do not uninstall Lardoon itself
        return True

    @override
    async def repair(self) -> bool:
        if self.is_installed():
            self.log.info(f"Deleting {self.name} installation ...")
            try:
                exe = self.get_exe_path()
                if exe and os.path.exists(exe):
                    os.remove(exe)
                self.log.info(f"{self.name} installation deleted.")
            except Exception as ex:
                self.log.warning(f"Failed to delete {self.name} installation: {ex}\nContinuing anyway ...")
        return await self.install()
