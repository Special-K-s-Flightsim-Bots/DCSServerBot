import asyncio
import atexit
import os
import subprocess

from core import Extension, Server, utils
from discord.ext import tasks
from extensions import TACVIEW_DEFAULT_DIR
from typing import Optional

# Globals
process: Optional[subprocess.Popen] = None
servers: set[str] = set()
imports: set[str] = set()


class Lardoon(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False
        if not process or process.returncode is not None:

            def run_subprocess():
                out = subprocess.DEVNULL if not self.config.get('debug', False) else None
                cmd = os.path.basename(self.config['cmd'])
                self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
                return subprocess.Popen([cmd, "serve", "--bind", self.config['bind']],
                                        executable=os.path.expandvars(self.config['cmd']),
                                        stdout=out, stderr=out)
            process = await asyncio.to_thread(run_subprocess)
            atexit.register(self.shutdown)
            servers.add(self.server.name)
        return self.is_running()

    def shutdown(self) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if process is not None and process.returncode is None and not servers:
            process.kill()
            process = None
            return super().shutdown()
        else:
            return True

    def is_running(self) -> bool:
        global process, servers

        if process is not None and process.poll() is None:
            return self.server.name in servers
        else:
            process = None
            return False

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
        def run_subprocess(cmd, args, out=None):
            subprocess.run([cmd] + args, stdout=out, stderr=out)

        minutes = self.config.get('minutes', 5)
        if self.schedule.minutes != minutes:
            self.schedule.change_interval(minutes=minutes)
        try:
            path = self.server.options['plugins']['Tacview'].get('tacviewExportPath', TACVIEW_DEFAULT_DIR)
            if not path:
                path = TACVIEW_DEFAULT_DIR
            cmd = os.path.expandvars(self.config['cmd'])
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None
            await asyncio.to_thread(run_subprocess, cmd, ["import", "-p", path], out)
            await asyncio.to_thread(run_subprocess, cmd, ["prune", "--no-dry-run"], out)
        except Exception as ex:
            self.log.exception(ex)
