from __future__ import annotations

import asyncio
import contextlib
import os
import psutil

from core import Server, Status, Coalition, utils
from extensions.srs import SRS
from services.music.radios import Radio
from services.music.tags import get_tag
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from services.music import MusicService

__all__ = ["SRSRadio"]


class SRSRadio(Radio):

    def __init__(self, service: MusicService, name: str, server: Server):
        super().__init__(service, name, server)
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
            # THE TRACK HAS TWO FACES: `current` is the title shown, `current_file` the path the
            # player reads tags from. A caller may pass either an absolute path (the queue worker)
            # or a music-directory-relative one, so both are normalised here — and the tag read gets
            # the ABSOLUTE path, or it would look for the file beside the process's own cwd.
            absolute = file if os.path.isabs(file) else os.path.join(self.service.music_dir, file)
            self.current_file = os.path.relpath(absolute, self.service.music_dir)
            # THE TITLE IS THE FILE'S STEM when nothing tags it — the same fallback the Discord
            # client shows, and the name a listener recognises; the suffix is not part of it.
            self.current = get_tag(absolute).title or os.path.splitext(os.path.basename(absolute))[0]
            # THE TRACK IS THERE — the one real "loaded" signal of this radio.
            self.end_loading()
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
        except psutil.NoSuchProcess:
            pass
        except Exception as ex:
            self.log.exception(ex)
        finally:
            # A CANCELLED PLAY MUST NOT LEAVE AUDIO RUNNING: when the queue worker is cancelled
            # mid-track, this block is reached with the process still alive, and dropping the
            # handle alone would keep the sound on with nothing left to stop it.
            process = self.process
            if process is not None and process.is_running():
                with contextlib.suppress(Exception):
                    process.terminate()
            self._forget_track()

    async def _terminate(self, process) -> None:
        """End one audio process, given the HANDLE to end.

        The handle is captured by the caller before anything is cancelled: cancelling the queue
        worker runs ``play()``'s ``finally``, which clears ``self.process`` — a stop that read the
        attribute AFTER the cancel found ``None`` and left the audio running. Taking the handle
        first is what makes Stop and Skip act on the process that is actually playing.
        """
        if process is None or not process.is_running():
            return
        try:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)
        except (psutil.TimeoutExpired, psutil.NoSuchProcess):
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()

    def _forget_track(self) -> None:
        self.current = None
        self.current_file = ""
        self.process = None

    async def skip(self) -> None:
        process = self.process
        await self._terminate(process)
        self._forget_track()

    async def stop(self) -> None:
        process = self.process
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
        await self._terminate(process)
        while self.queue_worker.is_running():
            await asyncio.sleep(0.5)
        self._forget_track()
        # a stopped radio is not loading
        self.end_loading()
