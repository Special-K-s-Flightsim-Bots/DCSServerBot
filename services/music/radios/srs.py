import asyncio
import os
from typing import cast

import psutil
import subprocess

from core import Server, Status, Coalition, utils, ProcessManager
from packaging.version import parse
from threading import Thread

from extensions.srs import SRS
from services.music.radios.base import RadioInitError, Radio
from plugins.music.utils import get_tag


class SRSRadio(Radio):

    def __init__(self, name: str, server: Server):
        super().__init__(name, server)
        self.process: psutil.Process | None = None

    async def play(self, file: str) -> None:
        if self.current and self.process:
            await self.skip()
        if self.server.status != Status.RUNNING:
            await self.stop()
            return

        self.log.debug(f"Playing {file} ...")

        extension = cast(SRS, self.server.extensions.get('SRS'))
        if not extension:
            self.log.error("SRS extension not found, can't play music.")
            return

        try:
            self.process = await extension.play_external_audio(self.config, file=file)
            coalition = Coalition.BLUE if int(self.config['coalition']) == 2 else Coalition.RED
            if 'popup' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                await self.server.sendPopupMessage(coalition, utils.format_string(self.config['popup'], **kwargs))
            if 'chat' in self.config:
                kwargs = self.config.copy()
                kwargs['song'] = get_tag(file).title or os.path.basename(file)
                await self.server.sendChatMessage(coalition, utils.format_string(self.config['chat'], **kwargs))
            await asyncio.to_thread(self.process.wait)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.current = None
            self.process = None

    async def skip(self) -> None:
        if self.process and self.process.is_running():
            self.process.terminate()
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
