import asyncio
import atexit
import os
import psutil
import subprocess

from core import Extension, Server, utils, get_translation
from discord.ext import tasks
from threading import Thread
from typing import Optional

from extensions.tacview import TACVIEW_DEFAULT_DIR

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "Lardoon"
]

# Globals
process: Optional[psutil.Process] = None
servers: set[str] = set()
imports: set[str] = set()
tacview_dirs: dict[str, set[str]] = {}
lock = asyncio.Lock()


class Lardoon(Extension):

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
            global process

            process = self.process = self.find_running_process(process)
        else:
            self.process = self.find_running_process(None)

    def find_running_process(self, p: Optional[psutil.Process] = None):
        if not p or not p.is_running():
            cmd = self.config.get('cmd')
            if not cmd:
                return None
            p = next(utils.find_process(os.path.basename(cmd), self.config['bind']), None)
            if p:
                self.log.debug("- Running Lardoon process found.")
            else:
                self.log.debug(f"- Running Lardoon process not found: {os.path.basename(cmd)} -bind {self.config['bind']}")
            return p
        else:
            return p

    def _get_tacview_dir(self) -> str:
        return self.config.get('tacviewExportPath', self.server.options['plugins']['Tacview'].get(
            'tacviewExportPath')) or TACVIEW_DEFAULT_DIR

    async def startup(self) -> bool:
        global lock

        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False

        async with lock:
            if not self.process or not self.process.is_running():
                def log_output(proc: subprocess.Popen):
                    for line in iter(proc.stdout.readline, b''):
                        self.log.info(line.decode('utf-8').rstrip())

                def run_subprocess():
                    if self.config.get('use_single_process', True):
                        cwd = None
                    else:
                        cwd = os.path.join(self.server.instance.home, 'Config')
                    out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
                    cmd = os.path.basename(self.config['cmd'])
                    self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
                    proc = subprocess.Popen([cmd, "serve", "--bind", self.config['bind']],
                                            executable=os.path.expandvars(self.config['cmd']), cwd=cwd,
                                            stdout=out, stderr=subprocess.STDOUT)
                    if self.config.get('debug', False):
                        Thread(target=log_output, args=(proc,), daemon=True).start()
                    return proc

                p = await asyncio.to_thread(run_subprocess)
                try:
                    self.process = psutil.Process(p.pid)
                    atexit.register(self.terminate)
                except psutil.NoSuchProcess:
                    self.log.error(f"Error during launch of {self.config['cmd']}!")
                    return False

        if self.config.get('use_single_process', True):
            global process, servers, tacview_dirs

            process = self.process
            servers.add(self.server.name)
            tacview_dir = self._get_tacview_dir()
            if tacview_dir not in tacview_dirs:
                tacview_dirs[tacview_dir] = set()
            tacview_dirs[tacview_dir].add(self.server.name)
        else:
            self._schedule.start()
        return await super().startup()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            if self.config.get('use_single_process', True):
                global process

                process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        super().shutdown()
        if self.config.get('use_single_process', True):
            global process, servers

            if self.server.name in servers:
                servers.remove(self.server.name)
            tacview_dir = self._get_tacview_dir()
            tacview_dirs[tacview_dir].discard(self.server.name)
            if not servers:
                return self.terminate()
            return True
        else:
            self._schedule.cancel()
            return self.terminate()

    def is_running(self) -> bool:
        if self.config.get('use_single_process', True):
            global process, servers

            return process is not None and process.is_running() and self.server.name in servers
        else:
            return self.process is not None and self.process.is_running()

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(self.config['cmd'])

    def is_installed(self) -> bool:
        if not super().is_installed():
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
            global lock, tacview_dirs

            for tacview_dir, server_list in tacview_dirs.items():
                if not server_list:
                    continue
                try:
                    async with lock:
                        self.log.debug("Lardoon: Scheduled import run ...")
                        await asyncio.to_thread(run_subprocess, ["import", "-p", tacview_dir])
                    async with lock:
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

    async def get_ports(self) -> dict:
        return {
            "Lardoon": self.config['bind'].split(':')[1]
        } if self.enabled else {}
