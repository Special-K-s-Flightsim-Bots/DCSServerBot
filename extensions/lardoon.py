import os
import subprocess
from core import Extension, report, DCSServerBot, Server
from datetime import datetime, timedelta
from typing import Optional

# Globals
process: Optional[subprocess.Popen] = None
prune: Optional[subprocess.Popen] = None
servers: set[str] = set()
imports: set[str] = set()


class Lardoon(Extension):

    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        super().__init__(bot, server, config)
        self.bot = bot
        self.log = bot.log
        self.server = server
        self.config = config
        self._import: Optional[subprocess.Popen] = None

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Lardoon needs Tacview to be enabled in your server!')
            return False
        if not process:
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(f"Launching Lardoon server with {cmd} serve --bind {self.config['bind']}")
            process = subprocess.Popen([cmd, "serve", "--bind", self.config['bind']],
                                       executable=os.path.expandvars(self.config['cmd']),
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            servers.add(self.server.name)
        return self.is_running()

    async def shutdown(self, data: dict) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if process and not servers:
            process.kill()
            process = None
            return await super().shutdown(data)
        else:
            return True

    def is_running(self) -> bool:
        global process, servers

        if process and process.poll() is None:
            return self.server.name in servers
        else:
            return False

    @property
    def version(self) -> str:
        return "0.0.11"

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

    async def schedule(self):
        global imports, prune

        # check if prune is running
        if prune:
            if prune.poll() is not None:
                prune = None

        # check if an active import job is running
        elif self._import:
            if self._import.poll() is not None:
                self._import = None
                imports.remove(self.server.name)
                if not imports:
                    # run prune
                    cmd = os.path.basename(self.config['cmd'])
                    prune = subprocess.Popen([cmd, "prune", "--no-dry-run"],
                                             executable=os.path.expandvars(self.config['cmd']),
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
        # run imports every 5 minutes
        elif self.lastrun > (datetime.now() - timedelta(minutes=self.config.get('minutes', 5))):
            try:
                path = self.server.options['plugins']['Tacview']['tacviewExportPath']
                cmd = os.path.basename(self.config['cmd'])
                self._import = subprocess.Popen([cmd, "import", "-p", path],
                                                executable=os.path.expandvars(self.config['cmd']),
                                                stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)
                imports.add(self.server.name)
            except KeyError:
                pass
            self.lastrun = datetime.now()
