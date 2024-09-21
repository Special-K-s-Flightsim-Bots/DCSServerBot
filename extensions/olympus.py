import asyncio
import os
import subprocess
import win32api

from core import Extension, Server, report, DCSServerBot
from typing import Optional

server_ports: dict[int, str] = dict()
client_ports: dict[int, str] = dict()


class Olympus(Extension):

    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        super().__init__(bot, server, config)
        installation = self.server.installation
        self.home = os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\services\Olympus'
        self.nodejs = os.path.join(os.path.expandvars(self.config.get('nodejs', '%ProgramFiles%\\nodejs')), 'node.exe')
        self.process = None

    @property
    def name(self) -> str:
        return "DCS Olympus"

    @property
    def version(self) -> Optional[str]:
        path = self.home + r'\bin\olympus.dll'
        if os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def is_installed(self) -> bool:
        global server_ports, client_ports

        if self.version != '1.0.3':
            self.log.error(f"DCS Olympus {self.version} not supported by this version of DCSServerBot!")
            return False
        if not os.path.exists(os.path.join(self.home, 'bin', 'olympus.dll')):
            self.log.warning(f"  => {self.server.name}: Can't load extension, {self.name} is not installed!")
            return False
        if not os.path.exists(self.nodejs):
            self.log.warning(f"  => {self.server.name}: Can't run {self.name}, Node.js is not installed!")
            return False
        server_port = self.config.get('server', {}).get('port', 3001)
        if server_ports.get(server_port, self.server.name) != self.server.name:
            self.log.warning(f'  => Server port {server_port} is already in use by another {self.name} instance!')
            return False
        server_ports[server_port] = self.server.name
        client_port = self.config.get('client', {}).get('port', 3000)
        if client_ports.get(client_port, self.server.name) != self.server.name:
            self.log.warning(f'  => Client port {client_port} is already in use by another {self.name} instance!')
            return False
        client_ports[client_port] = self.server.name
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = f"{self.bot.external_ip}:{self.config.get('client', {}).get('port', 3000)}"
        embed.add_field(name=self.name, value=value)

    async def prepare(self) -> bool:
        if not self.is_installed():
            return False
        self.log.debug(f"Launching {self.name} configurator ...")
        try:
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None
            subprocess.run([
                os.path.basename(self.nodejs),
                "configurator.js",
                "-a", self.config.get('server', {}).get('address', '0.0.0.0'),
                "-c", str(self.config.get('client', {}).get('port', 3000)),
                "-b", str(self.config.get('server', {}).get('port', 3001)),
                "-p", self.config.get('authentication', {}).get('gameMasterPassword', ''),
                "--bp", self.config.get('authentication', {}).get('blueCommanderPassword', ''),
                "--rp", self.config.get('authentication', {}).get('redCommanderPassword', '')
            ], executable=self.nodejs, cwd=os.path.join(self.home, 'client'), stdout=out, stderr=out)
            return await super().prepare()
        except Exception as ex:
            self.log.exception(ex)
            return False

    async def startup(self) -> bool:
        await super().startup()
        out = subprocess.DEVNULL if not self.config.get('debug', False) else None
        self.process = await asyncio.create_subprocess_exec(
            self.nodejs, r".\bin\www", cwd=os.path.join(self.home, "client"), stdout=out, stderr=out
        )
        return self.is_running()

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def shutdown(self, data: dict) -> bool:
        if self.is_running():
            self.process.terminate()
            await self.process.wait()
            self.process = None
        return await super().shutdown(data)
