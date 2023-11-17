import asyncio
import os

from core import Server, Status, Coalition, utils
from typing import Optional

from services.music.radios.base import RadioInitError, Radio
from plugins.music.utils import get_tag


class SRSRadio(Radio):

    def __init__(self, name: str, server: Server):
        super().__init__(name, server)
        self.process: Optional[asyncio.subprocess.Process] = None

    async def play(self, file: str) -> None:
        if self.current and self.process:
            await self.skip()
        if self.server.status != Status.RUNNING:
            await self.stop()
            return
        self.log.debug(f"Playing {file} ...")
        try:
            try:
                srs_inst = os.path.expandvars(self.server.extensions['SRS'].config['installation'])
                srs_port = self.server.extensions['SRS'].locals['Server Settings']['SERVER_PORT']
            except KeyError:
                raise RadioInitError("You need to set the SRS path in your nodes.yaml!")
            self.current = file
            self.process = await asyncio.create_subprocess_exec(
                os.path.join(srs_inst, "DCS-SR-ExternalAudio.exe"),
                "-f", self.config['frequency'],
                "-m", str(self.config['modulation']),
                "-c", str(self.config['coalition']),
                "-v", self.config.get('volume', '1.0'),
                "-p", srs_port,
                "-n", self.config.get('name', 'DCSSB MusicBox'),
                "-i", file,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            if 'popup' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                self.server.sendPopupMessage(Coalition.ALL, utils.format_string(self.config['popup'], **kwargs))
            if 'chat' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                self.server.sendChatMessage(Coalition.ALL, utils.format_string(self.config['popup'], **kwargs))
            await self.process.wait()
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.current = None

    async def skip(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            self.current = None

    async def stop(self) -> None:
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
            await self.skip()
            while self.queue_worker.is_running():
                await asyncio.sleep(0.5)
