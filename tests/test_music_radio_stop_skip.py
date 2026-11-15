"""Stop and Skip act on the audio process THAT IS PLAYING.

Cancelling the queue worker runs ``play()``'s ``finally``, which clears ``self.process``. A stop
that read the attribute after the cancel therefore found ``None`` and did nothing — the card showed
the new state while the music kept playing. The handle is now captured first, and a cancelled play
terminates the process it was leaving behind.
"""
import asyncio

import pytest

from services.music.radios.srs import SRSRadio


class _Process:
    def __init__(self):
        self.running = True
        self.terminated = 0

    def is_running(self) -> bool:
        return self.running

    def terminate(self) -> None:
        self.terminated += 1
        self.running = False

    def kill(self) -> None:
        self.running = False

    def wait(self, timeout=None) -> int:
        return 0


class _Worker:
    """A queue worker whose cancel() clears the radio's process handle, like the real one does."""

    def __init__(self, radio, running):
        self.radio = radio
        self.running = running
        self.cancelled = False

    def is_running(self) -> bool:
        return self.running

    def cancel(self) -> None:
        self.cancelled = True
        self.running = False
        self.radio.process = None


def _radio(process, worker_running=False):
    radio = object.__new__(SRSRadio)
    radio.process = process
    radio._current = "Paint It, Black"
    radio._current_file = "jungle/b.mp3"
    radio.queue_worker = _Worker(radio, worker_running)
    return radio


@pytest.mark.asyncio
async def test_skip_terminates_the_audio():
    process = _Process()
    radio = _radio(process)
    await radio.skip()
    assert process.terminated == 1
    assert radio.current == "" and radio.current_file == ""
    assert radio.process is None


@pytest.mark.asyncio
async def test_stop_terminates_the_audio_even_after_the_cancel_clears_the_handle():
    process = _Process()
    radio = _radio(process, worker_running=True)
    await radio.stop()
    assert radio.queue_worker.cancelled is True
    assert process.terminated == 1, "the handle captured before the cancel is the one that is ended"
    assert radio.current == ""


@pytest.mark.asyncio
async def test_stop_without_a_queue_still_terminates_the_audio():
    process = _Process()
    radio = _radio(process, worker_running=False)
    await radio.stop()
    assert process.terminated == 1


@pytest.mark.asyncio
async def test_a_stopped_radio_is_not_busy_waiting_forever():
    radio = _radio(_Process(), worker_running=True)
    await asyncio.wait_for(radio.stop(), timeout=2)
