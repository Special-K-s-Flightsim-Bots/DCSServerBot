from __future__ import annotations

import asyncio
import contextlib
import os
import psutil
import time

from core import Server, Status, Coalition, utils
from extensions.srs import SRS
from services.music.radios import Radio
from services.music.tags import get_tag
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from services.music import MusicService

__all__ = ["SRSRadio"]

#: How long one track waits for SRS before that attempt is given up. A server that registers as UP
#: loads its extensions in a background task and starts the SRS process in a second one, so a radio
#: asked to play at that moment is early by seconds; the cap keeps a radio whose SRS never comes up
#: from waiting forever. Overridable per radio with ``startup_wait`` in its music.yaml block.
SRS_STARTUP_WAIT_SECONDS = 60.0

#: How often the wait above looks again: one dict lookup, plus a liveness probe once the extension
#: is there.
SRS_STARTUP_POLL_SECONDS = 1.0


class SRSRadio(Radio):

    def __init__(self, service: MusicService, name: str, server: Server):
        super().__init__(service, name, server)
        self.process: psutil.Process | None = None

    @property
    def startup_wait(self) -> float:
        """Seconds one track may wait for SRS — the radio's ``startup_wait``, else the default."""
        try:
            return float(self.config.get('startup_wait', SRS_STARTUP_WAIT_SECONDS))
        except (TypeError, ValueError):
            return SRS_STARTUP_WAIT_SECONDS

    async def _answering(self, extension) -> bool:
        """Whether the extension's server answers — a probe that cannot end the queue.

        ``SRS.is_running()`` reads its own server section out of the SRS config file, so an
        installed but unconfigured SRS raises there. A readiness probe reports "not ready"; it does
        not take the radio down with it. An extension that carries no probe at all is not evidence
        of "not ready", so it is played to.
        """
        probe = getattr(extension, 'is_running', None)
        if not callable(probe):
            return True
        try:
            return bool(await asyncio.to_thread(probe))
        except Exception:
            return False

    async def _srs(self, timeout: float) -> SRS | None:
        """The SRS extension once it is LOADED and its server ANSWERS, or ``None`` at the deadline.

        Two independent lags produce the same symptom. The extension object does not exist until
        ``Server.init_extensions()`` has run — a background task when the server registers as UP —
        and the SRS process is launched by a second task that allows itself ten seconds to come up.
        Playing into either gap yields no music, so the track waits for the dependency instead of
        reporting it as missing. The lookup repeats on every poll, which also covers an SRS that is
        restarted while a radio is running.
        """
        deadline = time.monotonic() + timeout
        while True:
            extension = self.server.extensions.get('SRS')
            if extension is not None and await self._answering(extension):
                return cast(SRS, extension)
            if time.monotonic() >= deadline:
                return None
            # a load IS in flight, so the card says so for the whole wait and not only for the
            # first LOADING_CAP_SECONDS of it
            self.begin_loading()
            await asyncio.sleep(SRS_STARTUP_POLL_SECONDS)

    async def play(self, file: str) -> None:
        if self.server.status != Status.RUNNING:
            await self.stop()
            return

        extension = await self._srs(self.startup_wait)
        if extension is None:
            if 'SRS' in self.server.extensions:
                self.log.error("SRS is not answering, can't play music.")
            else:
                self.log.error("SRS extension not found, can't play music.")
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
