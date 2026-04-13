import asyncio
import contextlib
import os
import psutil

from core import Server, Status, Coalition, utils
from extensions.srs import SRS
from services.music.radios.base import Radio
from plugins.music.utils import get_tag
from typing import cast


class SRSRadio(Radio):

    def __init__(self, name: str, server: Server):
        super().__init__(name, server)
        self.process: psutil.Process | None = None

    async def play(self, file: str) -> None:
        extension = cast(SRS, self.server.extensions.get('SRS'))
        if not extension:
            self.log.error("SRS extension not found, can't play music.")
            return

        if self.server.status != Status.RUNNING:
            await self.stop()
            return

        # skip any song (if running)
        await self.skip()

        self.log.debug(f"Playing {file} ...")

        try:
            proc = await extension.play_external_audio(self.config, file=file)
            self.process = proc
            self.current = get_tag(file).title or os.path.basename(file)
            coalition = Coalition.BLUE if int(self.config['coalition']) == 2 else Coalition.RED
            server_config = self.service.get_config(self.server)
            if 'popup' in server_config:
                kwargs = self.config.copy()
                kwargs['song'] = self.current
                await self.server.sendPopupMessage(coalition, utils.format_string(server_config['popup'], **kwargs))
            if 'chat' in server_config:
                kwargs = self.config.copy()
                kwargs['song'] = self.current
                await self.server.sendChatMessage(Coalition.ALL, utils.format_string(server_config['chat'], **kwargs))
            await asyncio.to_thread(proc.wait)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.current = None
            self.process = None

    async def skip(self) -> None:
        if self.process and self.process.is_running():
            try:
                self.process.terminate()
                await asyncio.to_thread(self.process.wait, 5)
            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                with contextlib.suppress(psutil.NoSuchProcess):
                    self.process.kill()
            finally:
                self.current = None
                self.process = None

    async def stop(self) -> None:
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
            await self.skip()
            while self.queue_worker.is_running():
                await asyncio.sleep(0.5)
        else:
            await self.skip()
