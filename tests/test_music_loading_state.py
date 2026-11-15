"""A transport press reads as LOADING until the track is there, on both music surfaces.

Starting a track takes a moment (the audio process has to come up) and the transport is a plain form
POST: the browser navigates back and that render is the state BEFORE ``play()`` had a track, while the
live frames only push once the track changes. The pending state therefore lives on the SERVER, survives
the redirect render, and clears itself when the track spins up.

Pinned here, one section per criterion:

* the radio's own stamp — begun by the transport, ended by a track, by Stop, or by the 20 s cap (driven
  through an injected clock, never by sleeping);
* ``read_radios`` carries that stamp per entry, read from the same live object as ``on_air``;
* a refused start/skip (unknown radio, idle radio) stamps nothing, and a start that drives nothing
  stamps nothing;
* the rendered card: the state word is Loading…, the dot spins, Play and Skip are disabled with the
  why-text (Stop stays enabled), and both surfaces draw it from the ONE partial, from one fixture;
* a flip of the flag reaches the live stream as one frame, and the shell sheet carries the turn;
* the new msgids are translated in all four catalogs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from pathlib import Path

import pytest

from core import Status
from core.const import DEFAULT_TAG
from services.music import service as service_module
from services.music.radios import base as base_module
from services.music.radios import Mode
from services.music.radios.base import LOADING_CAP_SECONDS, Radio
from services.music.radios.srs import SRSRadio
from services.music.service import RADIO_IDLE, RADIO_OK, RADIO_UNKNOWN, MusicService
from services.webservice.pages import actions as actions_page
from services.webservice.pages import music as music_page
from services.webservice.pages import server_detail as detail_page

# the service doubles (a fake store, a fake live radio) and one server/node shape
from test_music_service import FakeRadio, Store, server as music_server
from test_music_service import service as service_over
# the real app's harness, and the readers this suite shares with the player's own pins
from test_webui_music_player import (  # noqa: F401 - fixtures imported on purpose
    _ORIGIN_INPUT, _app, _controls, _disabled, _drive, _player, _request, _scripted_sleep, _squeeze,
    _verb, _wrapper,
)
from test_webui_music_tab import (  # noqa: F401 - fixtures imported on purpose
    SERVER_A, FakeMusicService, clean_registry, client, config_dir, fleet, music_registry,
    music_service, radio as tab_radio, sign_in,
)
from test_webui_write_pause import open_page


# ------------------------------------------------------------------------- the clock and the doubles

class _Clock:
    """A clock a test moves by hand — the cap is proved without sleeping 20 s."""

    def __init__(self, start: float = 1000.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def tick(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def clock(monkeypatch):
    """Drive the stamp's clock: the module's one indirection, patched for the test."""
    ticking = _Clock()
    monkeypatch.setattr(base_module, "_now", ticking)
    return ticking


class _LoadingRadio(FakeRadio):
    """A live radio double carrying the loading stamp, recording the order of what was done to it."""

    def __init__(self, *args, loading: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._loading = loading

    def is_loading(self) -> bool:
        return self._loading

    def is_playing(self) -> bool:
        return False

    def begin_loading(self) -> None:
        self.calls.append("begin_loading")
        self._loading = True

    def end_loading(self) -> None:
        self.calls.append("end_loading")
        self._loading = False


def _bare_radio() -> Radio:
    """A radio carrying nothing but its state — the stamp lives on the base class."""
    return object.__new__(Radio)


class _Proc:
    """An audio process that keeps playing until it is terminated."""

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


class _Extension:
    def __init__(self):
        self.procs: list[_Proc] = []

    async def play_external_audio(self, config, *, file=None, text=None):
        proc = _Proc()
        self.procs.append(proc)
        return proc


class _PlayServer:
    def __init__(self, extension):
        self.name = "SRS-1"
        self.status = Status.RUNNING
        self.extensions = {"SRS": extension}


class _PlayService:
    def __init__(self, music_dir):
        self.music_dir = str(music_dir)
        self.log = logging.getLogger("test.music")

    def get_config(self, server, name=None):
        if name is None:
            return {}
        return {"coalition": 1, "frequency": "30.0", "modulation": "FM", "display_name": "R"}


def _srs_radio(tmp_path):
    """The real ``SRSRadio`` over a fake extension — its ``play()`` is what ends the stamp."""
    extension = _Extension()
    radio = object.__new__(SRSRadio)
    radio.name = "Radio 1"
    radio.service = _PlayService(tmp_path)
    radio.log = logging.getLogger("test.music")
    radio.server = _PlayServer(extension)
    radio.songs = []
    radio.idx = 0
    radio._playlist = "p"
    radio._mode = Mode.REPEAT
    radio._current = None
    radio._current_file = ""
    radio.process = None
    return radio, extension


async def _settle(predicate, timeout: float = 5.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


# ------------------------------------------------------------------- 1. the stamp and its four ends

def test_begin_loading_reports_a_load_in_flight(clock):
    radio = _bare_radio()
    assert radio.is_loading() is False, "an untouched radio is not loading"
    radio.begin_loading()
    assert radio.is_loading() is True


def test_the_cap_ends_a_load_that_never_lands(clock):
    """A launch that never yields a track must not leave a card loading forever."""
    radio = _bare_radio()
    radio.begin_loading()
    clock.tick(LOADING_CAP_SECONDS - 0.1)
    assert radio.is_loading() is True, "the cap must not cut a load short"
    clock.tick(0.1)
    assert radio.is_loading() is False, "the cap ends the state whatever happened"
    assert radio._loading_since is None, "the stamp is cleared, not merely ignored"


@pytest.mark.asyncio
async def test_a_track_arriving_ends_the_load(tmp_path):
    """`play()` sets the track — that IS the loaded signal, and it arrives before the audio ends."""
    (tmp_path / "a.mp3").write_bytes(b"")
    radio, extension = _srs_radio(tmp_path)
    radio.begin_loading()
    assert radio.is_loading() is True

    task = asyncio.create_task(radio.play(str(tmp_path / "a.mp3")))
    try:
        assert await _settle(lambda: radio.current != ""), "the track never got a face"
        assert radio.is_loading() is False, "the track is there, so the card must stop saying loading"
        assert extension.procs and extension.procs[0].is_running(), \
            "the stamp ended before the audio did — it tracks the track, not the process"
    finally:
        await radio.stop()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_ends_the_load(tmp_path):
    """A stopped radio is not loading — the stuck load's own cancel."""
    radio, _extension = _srs_radio(tmp_path)
    radio.begin_loading()
    assert radio.is_loading() is True
    await radio.stop()
    assert radio.is_loading() is False

    bare = _bare_radio()
    bare.begin_loading()
    await bare.stop()
    assert bare.is_loading() is False, "the base stop clears its own stamp too"


# ------------------------------------------------------------------ 2. the read that carries it

def _read_entry(monkeypatch, tmp_path, radios: dict):
    root = tmp_path / "music"
    root.mkdir()
    (root / "a.mp3").write_bytes(b"")
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": "Song", "artist": "Someone", "album": "Record",
                                      "duration": 42.0, "size": 9})
    built = service_over(Store(), root=str(root), radios={"SRS-1": radios})
    declared = built.locals[DEFAULT_TAG]["radios"]
    for name in radios:
        declared.setdefault(name, {"type": "SRSRadio", "mode": 1})
    return built


def test_read_radios_reports_the_load_of_the_radio_being_driven(tmp_path, monkeypatch):
    """`loading` rides the same entry as `on_air`, and both come from the same live object."""
    driven = _LoadingRadio(running=True, loading=True)
    driven.current = "Song"
    driven.current_file = "a.mp3"
    other = _LoadingRadio(running=False, loading=False)

    built = _read_entry(monkeypatch, tmp_path, {"Radio 1": driven, "Radio 2": other})
    entries = {entry["name"]: entry for entry in asyncio.run(built.read_radios(music_server()))}

    assert (entries["Radio 1"]["on_air"], entries["Radio 1"]["loading"]) == (True, True)
    assert (entries["Radio 2"]["on_air"], entries["Radio 2"]["loading"]) == (False, False)

    # the flag is READ from the live radio every time — not derived from on_air, which stays true
    driven.end_loading()
    again = {entry["name"]: entry for entry in asyncio.run(built.read_radios(music_server()))}
    assert (again["Radio 1"]["on_air"], again["Radio 1"]["loading"]) == (True, False)


# ------------------------------------------------------------------------ 3. the refusals stamp nothing

def test_a_refused_start_or_skip_never_stamps_a_load():
    driven = _LoadingRadio(loading=False)
    built = service_over(Store(), radios={"SRS-1": {"Radio 1": driven}})

    assert asyncio.run(MusicService.start_radio.__wrapped__(
        built, music_server(), "no-such-radio")) == RADIO_UNKNOWN
    assert asyncio.run(MusicService.skip_radio.__wrapped__(
        built, music_server(), "no-such-radio")) == RADIO_UNKNOWN

    idle = service_over(Store(), radios={})
    assert asyncio.run(MusicService.start_radio.__wrapped__(
        idle, music_server(), "Radio 1")) == RADIO_IDLE
    assert asyncio.run(MusicService.skip_radio.__wrapped__(
        idle, music_server(), "Radio 1")) == RADIO_IDLE
    assert driven.calls == [], "a refusal drove the radio"


def test_a_start_and_a_skip_stamp_the_load_before_they_drive():
    """The stamp goes on BEFORE the drive: the redirect render behind it is the only chance to say so."""
    stopped = _LoadingRadio(loading=False)
    built = service_over(Store(), radios={"SRS-1": {"Radio 1": stopped}})
    assert asyncio.run(MusicService.start_radio.__wrapped__(
        built, music_server(), "Radio 1")) == RADIO_OK
    assert stopped.calls == ["begin_loading", "start"], stopped.calls
    assert stopped.is_loading() is True

    playing = _LoadingRadio(running=True, loading=False)
    playing.current = "Song"
    playing.current_file = "a.mp3"
    built = service_over(Store(), radios={"SRS-1": {"Radio 1": playing}})
    assert asyncio.run(MusicService.skip_radio.__wrapped__(
        built, music_server(), "Radio 1")) == RADIO_OK
    assert playing.calls == ["begin_loading", "skip"], playing.calls
    assert playing.is_loading() is True


# ------------------------------------------------------------------------- 4. the rendered card

class _StampingMusic(FakeMusicService):
    """The tab's double with the SERVER-side stamp: the transport stamps, the read reports it.

    Without this the card pins below would be fed a hand-set flag; here the press is what sets it and
    the redirect render is what reads it — the reported symptom, end to end.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.live = _LoadingRadio(loading=False)

    async def read_radios(self, server_obj, refresh=False):
        rows = await super().read_radios(server_obj, refresh)
        for row in rows:
            row["loading"] = self.live.is_loading()
        return rows

    async def start_radio(self, server_obj, radio_name):
        self.controls.append(("play", radio_name))
        self.live.begin_loading()
        return ""

    async def skip_radio(self, server_obj, radio_name):
        self.controls.append(("skip", radio_name))
        self.live.begin_loading()
        return ""


@pytest.mark.asyncio
async def test_a_press_reads_as_loading_on_the_page_the_redirect_lands_on(
        config_dir, music_registry, music_service, clean_registry):
    """THE REPORTED SYMPTOM: press Play, and the page that comes back says the track is loading."""
    service = _StampingMusic({SERVER_A: [_radio_entry(loading=False)]})
    _install(service)
    app = _app(config_dir)
    tab_path = f"/fleet/{SERVER_A}?tab=music"
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, tab_path)
        card = _player(_wrapper(page, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
        assert f">{music_page.IDLE_WORD}<" in card, "the fixture does not start idle"

        play = [control for control in _controls(card) if _verb(control) == "play"][0]
        posted = dict(play["fields"])
        posted.pop("action", None)
        reply = await http.post(actions_page.MUSIC_CONTROL_PATH, data=posted, follow_redirects=False)
        while_loading = await open_page(http, reply.headers["location"])

        # the track lands: the radio's own play() clears the stamp here
        service.live.end_loading()
        after = await open_page(http, reply.headers["location"])

    assert reply.status_code == 303, reply.text
    assert reply.headers["location"] == tab_path
    assert service.controls == [("play", "srs-alpha")], service.controls
    card = _player(_wrapper(while_loading, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    assert f">{music_page.LOADING_WORD}<" in card, "the redirect render does not say loading"
    assert _disabled(card) == {"play": True, "stop": False, "skip": True, "mode": False}, card
    card = _player(_wrapper(after, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    assert music_page.LOADING_WORD not in card, "the card still says loading after the track landed"
    assert f">{music_page.IDLE_WORD}<" in card, "the settled state did not come back"


def _radio_entry(*, loading: bool = False, on_air: bool = False) -> dict:
    """One radio entry as ``read_radios`` answers it, with the load stamp set."""
    entry = tab_radio(on_air=on_air)
    entry["loading"] = loading
    return entry


def _install(service) -> None:
    from core.services.registry import ServiceRegistry

    ServiceRegistry._singletons[MusicService] = service


@pytest.mark.asyncio
async def test_both_surfaces_draw_the_loading_state_from_the_one_partial(
        config_dir, music_registry, music_service, clean_registry):
    """ONE fixture, two surfaces: the word, the turning dot, Play/Skip off, Stop still on."""
    _install(FakeMusicService({SERVER_A: [_radio_entry(loading=True)]}))
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        overview = await open_page(http, music_page.MUSIC_PATH)
        tab = await open_page(http, f"/fleet/{SERVER_A}?tab=music")

    cards = (_player(_wrapper(overview, music_page.RADIOS_TARGET), "srs-alpha"),
             _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha"))
    for card in cards:
        assert f">{music_page.LOADING_WORD}<" in card, f"the state word is not Loading…: {card}"
        assert 'class="dot spin"' in card, "the dot does not turn"
        assert ">on air<" not in card and ">idle<" not in card, "a settled state was drawn as well"
        assert _disabled(card) == {"play": True, "stop": False, "skip": True, "mode": False}, card
        titles = {_verb(control): control["button"]["title"] for control in _controls(card)}
        assert titles["play"] == music_page.LOADING_WHY, titles
        assert titles["skip"] == music_page.LOADING_WHY, titles
        for control in _controls(card):
            assert "<svg" in control["button"]["body"], "a disabled control lost its glyph"
        for marker in ("<progress", 'role="progressbar"', "pl-progress", "pl-elapsed"):
            assert marker not in card, f"a load has no measurable fraction: {marker}"

    # ONE partial: the two surfaces differ only in the origin the write returns to
    assert _squeeze(_ORIGIN_INPUT.sub("", cards[0])) == _squeeze(cards[1]), \
        "the two surfaces drew two different loading cards"


@pytest.mark.asyncio
async def test_a_load_that_ended_draws_the_settled_state_again(
        config_dir, music_registry, music_service, clean_registry):
    """The flag off puts the card back: the track's own state, Play off, Skip on."""
    _install(FakeMusicService({SERVER_A: [_radio_entry(loading=False, on_air=True)]}))
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        tab = await open_page(http, f"/fleet/{SERVER_A}?tab=music")

    card = _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    assert ">on air<" in card
    assert "spin" not in card, "the dot still turns with no load in flight"
    assert _disabled(card) == {"play": True, "stop": False, "skip": False, "mode": False}
    assert music_page.LOADING_WHY not in card


def test_the_shell_sheet_carries_the_turn():
    """The class the card wears is drawn where both surfaces load their look."""
    from services.webservice import shell

    css = (Path(shell.__file__).resolve().parent / "static" / "shell.css").read_text("utf-8")
    assert ".dot.spin" in css, "the sheet draws no loading dot"
    assert "animation: dot-spin" in css, "the loading dot does not turn"


# ------------------------------------------------------------------------------- 5. the live frame

@pytest.mark.asyncio
async def test_a_loading_flip_reaches_the_stream_as_one_frame(
        config_dir, music_registry, music_service, clean_registry):
    """No new endpoint and no client script: the flip rides the existing frames."""
    from services.webservice.pages import live as live_page

    entry = _radio_entry(loading=False, on_air=True)
    _install(FakeMusicService({SERVER_A: [entry]}))
    app = _app(config_dir)

    def begin():
        entry["loading"] = True

    def land():
        entry["loading"] = False

    frames = live_page.stream_frames(
        render=detail_page.music_tab_render_for(_request(app), SERVER_A),
        config=music_page.music_live_config(app),
        registry=live_page.LiveRegistry(4),
        sleep=_scripted_sleep([begin, lambda: None, land]),
        monotonic=lambda: 0.0)

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot"), out[0][:80]
    assert len(out) == 3, [frame[:60] for frame in out]
    # a frame is JSON-encoded, so a non-ASCII word travels escaped — read it the way it is sent
    word = json.dumps(music_page.LOADING_WORD)[1:-1]
    assert word in out[1], "the flip to loading was not pushed"
    assert "dot spin" in out[1], "the pushed card does not carry the turning dot"
    assert word not in out[2], "the flip back was not pushed"
    assert "dot spin" not in out[2]


# ---------------------------------------------------------------------------------- 6. the catalogs

@pytest.mark.parametrize("language", ("de", "es", "ru", "cn"))
def test_the_loading_copy_is_translated_in_every_catalog(language):
    """Both new msgids are in the four catalogs AND compiled into the .mo gettext reads."""
    from services.webservice import i18n

    catalog = i18n.LOCALE_DIR / language / "LC_MESSAGES" / "webui.po"
    text = catalog.read_text("utf-8")
    translations = i18n.translations_for(language)
    for msgid in (music_page.LOADING_WORD, music_page.LOADING_WHY):
        assert f'msgid "{msgid}"' in text, f"{language}'s catalog carries no {msgid!r}"
        assert translations.gettext(msgid) != msgid, f"{language} does not translate {msgid!r}"
