import asyncio
import atexit
import os
import psutil
import subprocess

from core import Extension, Server, utils, get_translation, PortType, Port, ThreadSafeDict, ProcessManager
from discord.ext import tasks
from threading import Thread
from typing_extensions import override

from extensions.tacview import TACVIEW_DEFAULT_DIR

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "Lardoon"
]


class Lardoon(Extension):
    _process: psutil.Process | None = None
    _servers: set[str] = set()
    _tacview_dirs: dict[str, set[str]] = ThreadSafeDict()
    _lock = asyncio.Lock()

    CONFIG_DICT = {
        "bind": {
            "type": str,
            "label": _("Bind Address"),
            "placeholder": "ip:port",
            "required": True
        },
        "url": {
            "type": str,
            "label": _("URL")
        },
        "minutes": {
            "type": int,
            "label": _("Scan (min)")
        }
    }

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        if self.config.get('use_single_process', True):
            type(self)._process = self.process = self.find_running_process(type(self)._process)
        else:
            self.process = self.find_running_process(None)

    def find_running_process(self, p: psutil.Process | None = None):
        if not p or not p.is_running():
            cmd = self.config.get('cmd')
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
        return self.config.get('tacviewExportPath', self.server.options['plugins']['Tacview'].get(
            'tacviewExportPath')) or TACVIEW_DEFAULT_DIR

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False

        async with type(self)._lock:
            if self.config.get('use_single_process', True):
                self.process = type(self)._process

            if not self.process or not self.process.is_running():
                def log_stream(proc: subprocess.Popen, stream: str):
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
                    cmd = os.path.basename(self.config['cmd'])
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
                    self.log.error(f"Error during launch of {self.config['cmd']}!")
                    return False

        if self.config.get('use_single_process', True):
            type(self)._process = self.process
            type(self)._servers.add(self.server.name)
            tacview_dir = self._get_tacview_dir()
            if tacview_dir not in type(self)._tacview_dirs:
                type(self)._tacview_dirs[tacview_dir] = set()
            type(self)._tacview_dirs[tacview_dir].add(self.server.name)
        else:
            self._schedule.start()
        return await super().startup()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            if self.config.get('use_single_process', True):
                type(self)._process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
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
                return self.terminate()
            return True
        else:
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
        return utils.get_windows_version(self.config['cmd'])

    @override
    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        # check if Lardoon is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Lardoon executable not found!")
            return False
        return True

    @override
    async def render(self, param: dict | None = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return {
            "name": self.name,
            "version": self.version,
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
            if proc.returncode != 0:
                if stderr:
                    self.log.error(stderr.decode('utf-8'))
            if self.config.get('debug', False) and stdout:
                self.log.debug(stdout.decode('utf-8'))

        # make sure we're running on the correct schedule
        minutes = self.config.get('minutes', 5)
        if self._schedule.minutes != minutes:
            self._schedule.change_interval(minutes=minutes)

        cmd = os.path.expandvars(self.config['cmd'])
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
        if self.config.get('use_single_process', True):
            self._schedule.start()
        return

    @override
    def get_ports(self) -> dict[str, Port]:
        return {
            "Lardoon": Port(self.config['bind'].split(':')[1], PortType.TCP, public=True)
        } if self.enabled else {}
