import asyncio
import atexit
import os
import psutil
import subprocess

from core import Extension, Server, utils
from discord.ext import tasks
from typing import Optional

from .tacview import TACVIEW_DEFAULT_DIR

# Globals
process: Optional[psutil.Process] = None
servers: set[str] = set()
imports: set[str] = set()
tacview_dirs: dict[str, set[str]] = {}
lock = asyncio.Lock()


class Lardoon(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)

    def _get_tacview_dir(self) -> str:
        return self.config.get('tacviewExportPath',
                               self.server.options['plugins']['Tacview'].get('tacviewExportPath',
                                                                             TACVIEW_DEFAULT_DIR))

    async def startup(self) -> bool:
        global process, servers, tacview_dirs, lock

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False
        if not process or not process.is_running():

            def run_subprocess():
                out = subprocess.DEVNULL if not self.config.get('debug', False) else None
                cmd = os.path.basename(self.config['cmd'])
                self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
                return subprocess.Popen([cmd, "serve", "--bind", self.config['bind']],
                                        executable=os.path.expandvars(self.config['cmd']),
                                        stdout=out, stderr=out)
            p = await asyncio.to_thread(run_subprocess)
            try:
                process = psutil.Process(p.pid)
                atexit.register(self.shutdown)
            except psutil.NoSuchProcess:
                self.log.error(f"Error during launch of {self.config['cmd']}!")
                return False
        servers.add(self.server.name)
        tacview_dir = self._get_tacview_dir()
        if tacview_dir not in tacview_dirs:
            tacview_dirs[tacview_dir] = set()
        tacview_dirs[tacview_dir].add(self.server.name)
        return await asyncio.to_thread(self.is_running)

    def shutdown(self) -> bool:
        global process, servers

        super().shutdown()
        if self.server.name in servers:
            servers.remove(self.server.name)
        tacview_dir = self._get_tacview_dir()
        try:
            tacview_dirs[tacview_dir].remove(self.server.name)
        except KeyError:
            pass
        if not servers:
            try:
                utils.terminate_process(process)
                process = None
                return True
            except Exception as ex:
                self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
                return False
        return True

    def is_running(self) -> bool:
        global process, servers

        if not process or not process.is_running():
            cmd = os.path.basename(self.config['cmd'])
            process = utils.find_process(cmd)
        return process is not None and self.server.name in servers

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(self.config['cmd'])

    def is_installed(self) -> bool:
        # check if Lardoon is enabled
        if not self.config.get('enabled', True):
            return False
        # check if Lardoon is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Lardoon executable not found!")
            return False
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return {
            "name": "Lardoon",
            "version": self.version,
            "value": value
        }

    @tasks.loop(minutes=1.0)
    async def schedule(self):
        global lock, tacview_dirs

        def run_subprocess(args):
            subprocess.run([cmd] + args, stdout=out, stderr=out)

        for tacview_dir, server_list in tacview_dirs.items():
            if server_list and self.server.name == list(server_list)[0]:
                break
        else:
            return
        minutes = self.config.get('minutes', 5)
        if self.schedule.minutes != minutes:
            self.schedule.change_interval(minutes=minutes)
        try:
            cmd = os.path.expandvars(self.config['cmd'])
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None
            async with lock:
                self.log.debug("Lardoon: Scheduled import run ...")
                await asyncio.to_thread(run_subprocess, ["import", "-p", tacview_dir])
            async with lock:
                self.log.debug("Lardoon: Scheduled prune run ...")
                await asyncio.to_thread(run_subprocess, ["prune", "--no-dry-run"])
        except Exception as ex:
            self.log.exception(ex)
