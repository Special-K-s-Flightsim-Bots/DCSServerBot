"""A track waits for SRS instead of being dropped when the radio starts too early.

A server that registers as UP loads its extensions in a background task and starts the SRS process
in a second one, so a radio started by that registration (or by an operator pressing Play) is
routinely asked to play before either exists. These pins drive the real queue worker of ``SRSRadio``
and assert what a listener hears: the FIRST track of the playlist, once SRS is up.
"""
import asyncio
import logging

import pytest

from core import Status
from services.music.radios import Mode
from services.music.radios.srs import SRSRadio

WAIT = 5.0


class _FakeProc:
    """An audio process that plays until it is terminated."""

    def __init__(self):
        self._done = asyncio.Event()

    def is_running(self) -> bool:
        return not self._done.is_set()

    def terminate(self) -> None:
        self._done.set()

    def kill(self) -> None:
        self._done.set()

    def wait(self, timeout=None):
        return 0


class _FakeExtension:
    def __init__(self, answering: bool = True):
        self.answering = answering
        self.started: list[str] = []

    def is_running(self) -> bool:
        return self.answering

    async def play_external_audio(self, config, *, file=None, text=None):
        self.started.append(file)
        return _FakeProc()


class _Server:
    def __init__(self, extensions):
        self.name = "JSH Co-op"
        self.status = Status.RUNNING
        self.extensions = extensions


class _Service:
    def __init__(self, music_dir, wait=WAIT):
        self.music_dir = str(music_dir)
        self.log = logging.getLogger("test")
        self.wait = wait

    def get_config(self, server, name=None):
        if name is None:
            return {}
        return {"coalition": 1, "frequency": "30.0", "modulation": "FM", "display_name": "R",
                "startup_wait": self.wait}


class _Errors(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.messages.append(record.getMessage())


def _radio(tmp_path, songs, extensions, wait=WAIT):
    radio = object.__new__(SRSRadio)
    radio.name = "Radio 1"
    radio.service = _Service(tmp_path, wait)
    radio.log = logging.getLogger("test.radio")
    radio.server = _Server(extensions)
    radio.songs = list(songs)
    radio.idx = 0
    radio._playlist = "p"
    radio._mode = Mode.REPEAT
    radio._current = None
    radio._current_file = ""
    radio.process = None
    return radio


async def _settle(predicate, timeout=WAIT):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


@pytest.mark.asyncio
async def test_the_first_track_plays_once_the_extension_is_loaded(tmp_path):
    """The extension arrives a second into the radio's life — the playlist must start at track one."""
    songs = ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]
    for name in songs:
        (tmp_path / name).write_bytes(b"")
    extensions = {}
    radio = _radio(tmp_path, songs, extensions)
    errors = _Errors()
    radio.log.addHandler(errors)
    radio.log.setLevel(logging.DEBUG)

    await radio.start()
    try:
        await asyncio.sleep(1.5)          # the radio is asked to play, SRS is not there yet
        assert errors.messages == [], f"the radio reported the missing extension: {errors.messages}"

        extension = _FakeExtension()
        extensions["SRS"] = extension      # init_extensions() finished

        assert await _settle(lambda: bool(extension.started)), \
            "the extension arrived and the radio still never played a track"
        assert extension.started[0].endswith("a.mp3"), \
            f"the first tracks were dropped while waiting: {extension.started}"
    finally:
        radio.log.removeHandler(errors)
        await radio.stop()


@pytest.mark.asyncio
async def test_a_track_waits_for_srs_to_answer(tmp_path):
    """The extension object exists, the SRS process is still starting: no audio is launched into it."""
    for name in ("a.mp3",):
        (tmp_path / name).write_bytes(b"")
    extension = _FakeExtension(answering=False)
    radio = _radio(tmp_path, ["a.mp3"], {"SRS": extension})

    await radio.start()
    try:
        await asyncio.sleep(1.0)
        assert extension.started == [], "a track was launched into an SRS that does not answer yet"
        extension.answering = True
        assert await _settle(lambda: bool(extension.started)), "SRS came up and the radio stayed silent"
    finally:
        await radio.stop()


@pytest.mark.asyncio
async def test_stop_ends_the_queue_while_a_track_waits(tmp_path):
    """The wait is inside the queue worker, so Stop has to end it — not sit out the whole wait."""
    for name in ("a.mp3",):
        (tmp_path / name).write_bytes(b"")
    radio = _radio(tmp_path, ["a.mp3"], {}, wait=WAIT)

    await radio.start()
    await asyncio.sleep(0.5)              # the worker is inside the wait now
    await asyncio.wait_for(radio.stop(), timeout=3.0)
    assert radio.is_running() is False


@pytest.mark.asyncio
async def test_the_wait_is_bounded_and_reported(tmp_path):
    """A radio whose SRS never appears reports it once and gives the attempt up."""
    for name in ("a.mp3",):
        (tmp_path / name).write_bytes(b"")
    radio = _radio(tmp_path, ["a.mp3"], {}, wait=0.5)
    errors = _Errors()
    radio.log.addHandler(errors)
    radio.log.setLevel(logging.DEBUG)

    await radio.start()
    try:
        assert await _settle(lambda: bool(errors.messages), timeout=WAIT), \
            "a radio with no SRS never reported it"
        assert "SRS extension not found" in errors.messages[0]
    finally:
        radio.log.removeHandler(errors)
        await radio.stop()
