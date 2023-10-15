import asyncio
import os
import subprocess
import sys
if sys.platform == 'win32':
    import win32api

from core import Extension, report, Server
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
        self._import: Optional[asyncio.subprocess.Process] = None

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False
        if not process or process.returncode is not None:
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
            process = subprocess.Popen([cmd, "serve", "--bind", self.config['bind']],
                                       executable=os.path.expandvars(self.config['cmd']),
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            servers.add(self.server.name)
        return self.is_running()

    async def shutdown(self) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if process is not None and process.returncode is None and not servers:
            process.kill()
            process = None
            return await super().shutdown()
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
        if sys.platform == 'win32':
            info = win32api.GetFileVersionInfo(os.path.expandvars(self.config['cmd']), '\\')
            version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                       info['FileVersionMS'] % 65536,
                                       info['FileVersionLS'] / 65536,
                                       info['FileVersionLS'] % 65536)
        else:
            version = None
        return version

    def is_installed(self) -> bool:
        # check if Lardoon is enabled
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        # check if Lardoon is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Lardoon executable not found!")
            return False
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        embed.add_field(name='Lardoon', value=value)

    @tasks.loop(minutes=1.0)
    async def schedule(self):
        minutes = self.config.get('minutes', 5)
        if self.schedule.minutes != minutes:
            self.schedule.change_interval(minutes=minutes)
        try:
            path = self.server.options['plugins']['Tacview'].get('tacviewExportPath', TACVIEW_DEFAULT_DIR)
            if not path:
                path = TACVIEW_DEFAULT_DIR
            cmd = os.path.expandvars(self.config['cmd'])
            proc = await asyncio.create_subprocess_exec(
                cmd,  "import", "-p", path, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.communicate()
            proc = await asyncio.create_subprocess_exec(
                cmd, "prune",  "--no-dry-run", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.communicate()
        except Exception as ex:
            self.log.exception(ex)
