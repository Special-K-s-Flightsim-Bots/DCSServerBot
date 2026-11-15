"""Skip must move the queue to the NEXT track, end to end.

A stub cannot show this: the question is what the real queue worker does after ``skip()`` ends the
track that is playing. This drives the real ``start()`` → worker → ``play()`` → ``skip()`` → next
track cycle of ``SRSRadio`` with a fake SRS extension that plays until it is terminated.
"""
import asyncio
import logging
import threading

import pytest

from core import Status
from services.music.radios import Mode
from services.music.radios.srs import SRSRadio


class _FakeProc:
    """An audio process that plays until it is terminated."""

    def __init__(self):
        self._done = threading.Event()
        self.terminated = False

    def is_running(self) -> bool:
        return not self._done.is_set()

    def terminate(self) -> None:
        self.terminated = True
        self._done.set()

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0


class _FakeExtension:
    def __init__(self):
        self.started: list[str] = []
        self.procs: list[_FakeProc] = []

    async def play_external_audio(self, config, *, file=None, text=None):
        self.started.append(file)
        proc = _FakeProc()
        self.procs.append(proc)
        return proc


class _Server:
    def __init__(self, extension):
        self.name = "SRS-1"
        self.status = Status.RUNNING
        self.extensions = {"SRS": extension}


class _Service:
    def __init__(self, music_dir):
        self.music_dir = str(music_dir)
        self.log = logging.getLogger("test")

    def get_config(self, server, name=None):
        if name is None:
            return {}
        return {"coalition": 1, "frequency": "30.0", "modulation": "FM", "display_name": "R"}


def _radio(tmp_path, songs):
    extension = _FakeExtension()
    radio = object.__new__(SRSRadio)
    radio.name = "Radio 1"
    radio.service = _Service(tmp_path)
    radio.log = logging.getLogger("test")
    radio.server = _Server(extension)
    radio.songs = list(songs)
    radio.idx = 0
    radio._playlist = "p"
    radio._mode = Mode.REPEAT
    radio._current = None
    radio._current_file = ""
    radio.process = None
    return radio, extension


async def _settle(predicate, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


@pytest.mark.asyncio
async def test_skip_advances_to_the_next_track(tmp_path):
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).write_bytes(b"")
    radio, extension = _radio(tmp_path, ["a.mp3", "b.mp3"])

    await radio.start()
    try:
        assert await _settle(lambda: len(extension.started) == 1), "the queue never started a track"
        assert extension.started[0].endswith("a.mp3")

        await radio.skip()

        assert await _settle(lambda: len(extension.started) == 2), \
            "skip ended the track but the queue never started the next one"
        assert extension.started[1].endswith("b.mp3")
    finally:
        await radio.stop()


@pytest.mark.asyncio
async def test_stop_ends_the_track_and_the_queue(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"")
    radio, extension = _radio(tmp_path, ["a.mp3"])

    await radio.start()
    try:
        assert await _settle(lambda: len(extension.started) == 1)
        await radio.stop()
        assert extension.procs[0].terminated is True
        assert radio.is_running() is False
        assert radio.current == ""
    finally:
        await radio.stop()


class _ServiceSelf:
    """The little a service method touches: the declared radios and the live ones."""

    def __init__(self, server, decl, live):
        self.radios_declared = lambda s: decl
        self.radios = {server.name: live}


@pytest.mark.asyncio
async def test_skipping_a_track_played_outside_the_queue_resumes_the_playlist(tmp_path):
    """A direct play has no queue behind it; skip must not leave the radio silent."""
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).write_bytes(b"")
    radio, extension = _radio(tmp_path, ["a.mp3", "b.mp3"])

    # playing, but started outside the queue: no worker, just a live process
    radio.process = await extension.play_external_audio({}, file=str(tmp_path / "a.mp3"))
    radio.current = "a"
    radio.current_file = "a.mp3"
    assert radio.is_running() is False

    from services.music.service import MusicService

    reason = await MusicService.skip_radio.__wrapped__(
        _ServiceSelf(radio.server, {"Radio 1": {}}, {"Radio 1": radio}), radio.server, "Radio 1")
    try:
        assert reason == ""
        assert await _settle(lambda: len(extension.started) >= 2), \
            "skip ended the direct play and the playlist never resumed"
        assert radio.is_running() is True
    finally:
        await radio.stop()
