"""On air means AUDIO, not just the queue.

A radio can play a single file without ever starting its queue worker (`play(file)` is reachable on
its own). The read that feeds every card asked `is_running()` — the worker — so a radio that was
audibly playing reported not on air: Play stayed enabled and Stop/Skip stayed disabled.
"""
from services.music.radios.base import Radio


class _Process:
    def __init__(self, running: bool):
        self._running = running

    def is_running(self) -> bool:
        return self._running


def _radio_with(process):
    radio = object.__new__(Radio)
    radio.process = process
    return radio


def test_audio_playing_without_a_queue_worker_is_on_air():
    radio = _radio_with(_Process(True))
    assert radio.is_playing() is True, "the audio process is what the listener hears"
    assert radio.is_running() is False, "no worker was started — the queue state is unchanged"


def test_a_stopped_process_is_not_playing():
    assert _radio_with(_Process(False)).is_playing() is False
    assert _radio_with(None).is_playing() is False


def test_is_running_still_means_the_queue_for_control_flow():
    """`start_radio` reads this: a direct play must not make it skip starting a real worker."""
    radio = _radio_with(_Process(True))
    assert radio.is_running() is False


def test_a_read_that_prints_a_track_says_on_air_even_when_both_flags_are_dark():
    """THE INVARIANT: a card shows a track from `current`/`current_file`; if it prints one, the
    state must say on air — otherwise the only enabled control is Play, which cannot act."""
    from services.music.service import _radio_on_air

    radio = _radio_with(None)          # no audio handle
    radio.is_running = lambda: False   # no queue worker
    radio.is_playing = lambda: False   # and, as far as this process can see, no audio
    assert _radio_on_air(radio, "Paint It, Black", "jungle/b.mp3") is True
    assert _radio_on_air(radio, "", "") is False
    assert _radio_on_air(None, "Paint It, Black", "jungle/b.mp3") is False



def test_a_track_whose_tag_carries_no_title_prints_its_file_name():
    """An .ogg without a TITLE comment (or with one this reader cannot parse) still has a name."""
    from services.music.service import _track_title

    assert _track_title({}, "", "jungle/Paint It, Black.ogg") == "Paint It, Black"
    assert _track_title({"title": "Tagged"}, "", "jungle/b.ogg") == "Tagged"
    assert _track_title({}, "Radio Title", "") == "Radio Title"
