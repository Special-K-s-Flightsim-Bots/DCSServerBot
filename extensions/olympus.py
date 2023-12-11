import asyncio
import os
import subprocess

from core import Extension, utils, Server
from typing import Optional


class Olympus(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'Olympus')
        self.process = None

    @property
    def name(self) -> str:
        return "DCS Olympus"

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(os.path.join(self.home, 'Mods', 'Services', 'Olympus', 'bin', 'olympus.dll'))

    def is_installed(self) -> bool:
        return os.path.exists(self.home)

    async def render(self, param: Optional[dict] = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = f"{self.node.public_ip}:{self.config.get('client', {}).get('port', 3000)}"
        return {
            "name": "Olympus",
            "version": self.version or 'n/a',
            "value": value
        }

    async def prepare(self) -> bool:
        cmd = 'configurator.exe'
        self.log.debug(f"Launching Olympus configurator ...")
        try:
            subprocess.run([
                cmd,
                "-a", self.config.get('server', {}).get('address', '0.0.0.0'),
                "-c", str(self.config.get('client', {}).get('port', 3000)),
                "-b", str(self.config.get('server', {}).get('port', 3001)),
                "-p", self.config.get('authentication', {}).get('gameMasterPassword', ''),
                "-bp", self.config.get('authentication', {}).get('blueCommanderPassword', ''),
                "-rp", self.config.get('authentication', {}).get('redCommanderPassword', '')
            ], executable=os.path.join(self.home, cmd), cwd=self.home, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            return await super().prepare()
        except Exception as ex:
            self.log.exception(ex)
            return False

    async def startup(self) -> bool:
        await super().startup()
        self.process = await asyncio.create_subprocess_exec(
            os.path.join(self.home, "client", "node.exe"), r".\bin\www",
            cwd=os.path.join(self.home, "client"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        return self.is_running()

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def shutdown(self) -> bool:
        if self.is_running():
            self.process.terminate()
            await self.process.wait()
            self.process = None
        return await super().shutdown()
