import asyncio
import json
import os
import stat
import subprocess
import sys

from core import Extension, utils, Server
from typing import Optional

server_ports: dict[int, str] = dict()
client_ports: dict[int, str] = dict()


class Olympus(Extension):

    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'Olympus')
        super().__init__(server, config)
        self.nodejs = os.path.join(os.path.expandvars(self.config.get('nodejs', '%ProgramFiles%\\nodejs')), 'node.exe')
        self.process = None

    @property
    def name(self) -> str:
        return "DCS Olympus"

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(os.path.join(self.home, 'bin', 'olympus.dll'))

    def load_config(self) -> Optional[dict]:
        try:
            with open(os.path.join(self.home, 'olympus.json'), 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            return {}

    def is_installed(self) -> bool:
        if not os.path.exists(os.path.join(self.home, 'bin', 'olympus.dll')):
            self.log.warning(f"  => {self.server.name}: Can't load extension, {self.name} is not installed!")
            return False
        if not os.path.exists(self.nodejs):
            self.log.warning(f"  => {self.server.name}: Can't run {self.name}, Node.js is not installed!")
            return False
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = f"http://{self.node.public_ip}:{self.config.get('client', {}).get('port', 3000)}"
        return {
            "name": self.name,
            "version": self.version,
            "value": value
        }

    async def prepare(self) -> bool:
        global server_ports, client_ports

        if not self.is_installed():
            return False
        self.log.debug(f"Launching {self.name} configurator ...")
        try:
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None
            try:
                os.chmod(os.path.join(self.home, 'olympus.json'), stat.S_IWUSR)
            except PermissionError:
                self.log.warning(
                    f"  => {self.server.name}: No write permission on olympus.json, skipping {self.name}.")
                return False
            server_port = self.locals.get('server', {}).get('port', 3001)
            if server_ports.get(server_port, self.server.name) != self.server.name:
                self.log.error(f"  => {self.server.name}: {self.name} server.port {server_port} already in use by "
                               f"server {server_ports[server_port]}!")
                return False
            server_ports[server_port] = self.server.name
            client_port = self.locals.get('client', {}).get('port', 3000)
            if client_ports.get(client_port, self.server.name) != self.server.name:
                self.log.error(f"  => {self.server.name}: {self.name} client.port {client_port} already in use by "
                               f"server {client_ports[client_port]}!")
                return False
            client_ports[client_port] = self.server.name
            # Starting Olympus Configurator
            subprocess.run([
                os.path.basename(self.nodejs),
                "configurator.js",
                "-a", self.config.get('server', {}).get('address', '0.0.0.0'),
                "-c", str(client_port),
                "-b", str(server_port),
                "-p", self.config.get('authentication', {}).get('gameMasterPassword', ''),
                "--bp", self.config.get('authentication', {}).get('blueCommanderPassword', ''),
                "--rp", self.config.get('authentication', {}).get('redCommanderPassword', '')
            ], executable=self.nodejs, cwd=os.path.join(self.home, 'client'), stdout=out, stderr=out)
            self.locals = self.load_config()
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
        if sys.platform == 'win32':
            from os import system
            system(f"title DCSServerBot v{self.server.node.bot_version}.{self.server.node.sub_version}")
        return self.is_running()

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def shutdown(self) -> bool:
        await super().shutdown()
        if self.is_running():
            self.process.terminate()
            await self.process.wait()
            self.process = None
        return True
