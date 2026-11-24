"""A music transport write returns to the page that issued it, and the buttons follow it.

Three questions, each driven through the REAL application:

* the radio card's forms carry the page they were drawn on as their origin
  (``pages/actions.ORIGIN_FIELD``), so the shared control route can answer them HERE;
* a successful play lands back on ``/music`` — not on the server's Music tab — and the page it lands
  on has re-read the radios: Play is disabled, Stop and Skip are enabled;
* the reverse after a Stop, on the page AND in the streamed fragment (the three-second live stream
  sends the same partial the page drew, so both must read the one state);
* the SERVER's Music tab keeps its own default: its forms name no origin and the write returns to the
  tab, whose re-render shows the new state.

The domain boundary is a music service double that MODELS a radio: ``start_radio`` puts it on air and
``stop_radio`` takes it off, so the read answers the state the write produced — which is what the real
``MusicService`` does (``read_radios`` reports ``radio.is_running()``). A double whose reads never
change would make every assertion below pass without proving anything.
"""
from __future__ import annotations

import re

import pytest

from core.services.registry import ServiceRegistry
from services.music.service import MusicService
from services.webservice.pages import actions as actions_page
from services.webservice.pages import music as music_page

# the music harness (the real app, the local login, the stub cluster) and its doubles
from test_webui_music import FakeMusic, music_registry  # noqa: F401 - fixtures on purpose
from test_webui_music_tab import (  # noqa: F401 - fixtures/helpers on purpose
    SERVER_A, FakeMusicService, build, clean_registry, client, config_dir, fleet, music_service,
    radio, sign_in,
)

_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)
_INPUT = re.compile(r"<input\b([^>]*)>")
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_ARTICLE = re.compile(r"<article\b", re.S)


class FollowingMusic(FakeMusic):
    """The ``/music`` double, but a transport write changes what the read answers next."""

    def _entry(self, server, radio_name):
        for entry in self.radios.get(getattr(server, "name", server), []):
            if entry["name"] == radio_name:
                return entry
        return None

    async def start_radio(self, server, radio_name):
        self.calls.append(("start", (getattr(server, "name", server), radio_name)))
        entry = self._entry(server, radio_name)
        if entry is not None:
            entry["on_air"] = True
            entry["current"] = {"title": "Paint It Black", "artist": "The Rolling Stones",
                                "album": "", "file": "jungle/b.mp3", "duration": 226.0}
        return ""

    async def stop_radio(self, server, radio_name):
        self.calls.append(("stop", (getattr(server, "name", server), radio_name)))
        entry = self._entry(server, radio_name)
        if entry is not None:
            entry["on_air"] = False
            entry["current"] = {}
        return ""

    async def skip_radio(self, server, radio_name):
        self.calls.append(("skip", (getattr(server, "name", server), radio_name)))
        return ""


class FollowingTabMusic(FakeMusicService):
    """The TAB's double, with the same radio model: a write moves the state the read reports."""

    def _entry(self, server_obj, radio_name):
        for entry in self._radios.get(server_obj.name, []):
            if entry["name"] == radio_name:
                return entry
        return None

    async def start_radio(self, server_obj, radio_name):
        self.controls.append(("play", radio_name))
        entry = self._entry(server_obj, radio_name)
        if entry is not None:
            entry.update({"on_air": True,
                          "current": {"title": "Fortunate Son", "artist": "Creedence", "album": "",
                                      "file": f"{radio_name}/01.mp3", "duration": 183.0}})
        return ""

    async def stop_radio(self, server_obj, radio_name):
        self.controls.append(("stop", radio_name))
        entry = self._entry(server_obj, radio_name)
        if entry is not None:
            entry.update({"on_air": False, "current": {}})
        return ""


async def _open(http, path=music_page.MUSIC_PATH) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


async def _fragments(http) -> dict:
    """The JSON the polling fallback reads — the two fragments exactly as the stream renders them."""
    response = await http.get(music_page.MUSIC_FRAGMENTS_PATH, headers={"accept": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()


def _forms(html: str) -> list[dict]:
    """Every form of *html* as its attributes plus the hidden fields it posts."""
    found: list[dict] = []
    for attrs, body in _FORM.findall(html):
        attributes = dict(_ATTR.findall(attrs))
        fields: dict[str, str] = {}
        for raw in _INPUT.findall(body):
            named = dict(_ATTR.findall(raw))
            if named.get("name"):
                fields[named["name"]] = named.get("value", "")
        attributes["fields"] = fields
        attributes["body"] = body
        found.append(attributes)
    return found


def _control_forms(html: str) -> list[dict]:
    """The transport forms of a fragment — the ones posting the shared control route."""
    return [form for form in _forms(html)
            if form.get("action") == actions_page.MUSIC_CONTROL_PATH]


def _verb(form: dict) -> str:
    return form["fields"].get(actions_page.MUSIC_CONTROL_FIELD, "")


def _card(html: str, name: str) -> str:
    """The article of ONE radio card, keyed on the card's own name element."""
    for chunk in _ARTICLE.split(html)[1:]:
        body = chunk.split("</article>")[0]
        match = re.search(r'<b class="pl-name">(.*?)</b>', body, re.S)
        if match and re.sub(r"\s+", " ", match.group(1)).strip() == name:
            return body
    raise AssertionError(f"no card named {name!r} in the fragment")


def _disabled(card: str) -> dict[str, bool]:
    """Whether each transport verb of *card* is drawn disabled."""
    stated: dict[str, bool] = {}
    for form in _control_forms(card):
        stated[_verb(form)] = "disabled" in form["body"]
    return stated


@pytest.fixture
def following_music():
    """The ``/music`` double, registered under the real service class for this test only."""
    service = FollowingMusic()
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved


@pytest.fixture
def following_tab_music():
    """The TAB's double, registered the same way."""
    service = FollowingTabMusic({SERVER_A: [radio()]})
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved


def _app(config_dir, *, plugins=("music",)):
    return build(config_dir, fleet(), plugins=plugins)


# ------------------------------------------------- 1. the origin the card's forms carry

@pytest.mark.asyncio
async def test_every_transport_form_carries_the_music_page_as_its_origin(
        config_dir, music_registry, following_music):
    """Play, Stop, Skip and the mode pick post ``/music``, so the write can answer this page."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await _open(http)

    forms = _control_forms(page)
    assert [_verb(form) for form in forms] == ["play", "stop", "skip", "mode"], forms
    for form in forms:
        assert form["fields"][actions_page.ORIGIN_FIELD] == music_page.MUSIC_PATH, form["fields"]
    # the same fields the Music tab posts, plus the origin — not a second write
    assert {form["fields"].get(actions_page.SERVER_CONFIG_FIELD) for form in forms} == {"SRS-1"}
    assert {form["fields"].get(actions_page.MUSIC_RADIO_FIELD) for form in forms} == {"Radio 1"}


# ------------------------------------- 2. a play lands back on /music with the card flipped

@pytest.mark.asyncio
async def test_a_play_returns_to_the_music_page_and_the_card_shows_it_on_air(
        config_dir, music_registry, following_music):
    """The write returns HERE, and the page it returns to has re-read the radio's own state.

    Three facts on one round trip: the 303 names ``/music``, that page's card has Play disabled and
    Stop/Skip enabled, and the streamed fragment carries the SAME markup — so the 3 s tick cannot
    show what the page did not.
    """
    for entry in following_music.radios["SRS-1"]:      # this pin starts from an IDLE radio
        entry.update({"on_air": False, "current": {}})
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await _open(http)
        play = [form for form in _control_forms(page) if _verb(form) == "play"][0]
        assert _disabled(_card(page, "Radio 1"))["play"] is False, "the fixture starts idle"
        posted = dict(play["fields"])
        reply = await http.post(play["action"], data=posted)
        landed = await _open(http, reply.headers["location"])
        fragments = await _fragments(http)

    assert following_music.calls == [("start", ("SRS-1", "Radio 1"))], following_music.calls
    assert reply.status_code == 303, reply.text
    assert reply.headers["location"] == music_page.MUSIC_PATH, \
        "the transport write left the page it was pressed on"
    states = _disabled(_card(landed, "Radio 1"))
    assert states == {"play": True, "stop": False, "skip": False, "mode": False}, states
    assert music_page.ON_AIR_WORD in _card(landed, "Radio 1")
    streamed = _disabled(_card(fragments[music_page.RADIOS_TARGET], "Radio 1"))
    assert streamed == states, f"the streamed card disagrees with the page: {streamed}"


@pytest.mark.asyncio
async def test_a_stop_returns_to_the_music_page_and_the_card_shows_it_idle(
        config_dir, music_registry, following_music):
    """The reverse direction, on the same round trip: Play offered again, Stop and Skip withheld."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await _open(http)
        assert _disabled(_card(page, "Radio 1"))["play"] is True, "the fixture starts on air"
        stop = [form for form in _control_forms(page) if _verb(form) == "stop"][0]
        reply = await http.post(stop["action"], data=dict(stop["fields"]))
        landed = await _open(http, reply.headers["location"])
        fragments = await _fragments(http)

    assert following_music.calls == [("stop", ("SRS-1", "Radio 1"))], following_music.calls
    assert reply.headers["location"] == music_page.MUSIC_PATH
    states = _disabled(_card(landed, "Radio 1"))
    assert states == {"play": False, "stop": True, "skip": True, "mode": False}, states
    assert music_page.IDLE_WORD in _card(landed, "Radio 1")
    assert _disabled(_card(fragments[music_page.RADIOS_TARGET], "Radio 1")) == states


# ------------------------------------------- 3. the server's Music tab keeps its own default

@pytest.mark.asyncio
async def test_a_transport_write_from_the_music_tab_still_returns_to_the_tab(
        config_dir, music_registry, following_tab_music, clean_registry):
    """The tab's forms name no origin, so the write keeps the tab as its destination — and the tab
    re-renders from the read, so its buttons follow the write too."""
    app = _app(config_dir, plugins=("mission", "music"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await _open(http, f"/fleet/{SERVER_A}?tab=music")
        play = [form for form in _control_forms(page) if _verb(form) == "play"][0]
        assert actions_page.ORIGIN_FIELD not in play["fields"], \
            "the tab's form names an origin it must not: the tab IS the default"
        assert "disabled" not in play["body"], "the fixture's radio starts idle: Play is offered"
        stop = [form for form in _control_forms(page) if _verb(form) == "stop"][0]
        assert "disabled" in stop["body"], "an idle radio's Stop is disabled"
        reply = await http.post(play["action"], data=dict(play["fields"]))
        landed = await _open(http, reply.headers["location"])

    assert following_tab_music.controls == [("play", radio()["name"])]
    assert reply.status_code == 303
    assert reply.headers["location"] == f"/fleet/{SERVER_A}?tab=music"
    assert "This radio is already on air." in landed, "the tab's card was not re-rendered"
    assert 'value="play">Play</button>' not in landed, "Play is still offered on an air radio"
