"""The music player: ONE glyph player on both surfaces, and a live Servers > Music tab.

One section per acceptance criterion of the card, every one driven through the REAL application (a
request, not a hand-built render):

* the /music card and the Servers > Music tab draw the SAME partial — `_music_player.html`, through the
  ONE builder `pages.music.radio_cards` — so the two surfaces carry identical markup (the origin field
  of a transport form is the only per-surface fact, and it is dropped before the comparison);
* the transport is four GLYPH buttons with `aria-label` + `title`, offers no pause verb, disables each
  verb its radio's state makes meaningless (with the sentence that says why), and is absent entirely
  for a caller without `music.control`;
* no progress element is rendered: the radio read carries a duration and no position;
* the tab's stream is gated with `music.view` AND the server's own scope, opens with a snapshot, then
  re-emits only the region whose HTML changed — and a successful skip moves the next fragment to the
  new track;
* New Playlist is the shared accent button and is still gated by the write capability.

The domain boundary is the tab's own ``MusicService`` double (``test_webui_music_tab``), extended here
to MODEL a skip: a write that does not move what the read answers would make every assertion below
pass without proving anything.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.webservice import permissions
from services.webservice.auth import SESSION_IDENTITY_KEY
from services.webservice.pages import actions as actions_page
from services.webservice.pages import live as live_page
from services.webservice.pages import music as music_page
from services.webservice.pages import server_detail as detail_page

# the tab's harness (the real app, the local login, the stub cluster) and its music double
from test_webui_music_tab import (  # noqa: F401 - fixtures imported on purpose
    SERVER_A, SERVER_B, FakeMusicService, build, clean_registry, client, config_dir, fleet,
    music_registry, music_service, radio, sign_in,
)
from test_webui_music_live import _declare_hoster, _hoster_users  # noqa: F401 - helpers
from test_webui_write_pause import open_page, token_of

PLAYER_MARKER = 'data-music-player'
PLAYER = "_music_player.html"
TEMPLATES = (Path(detail_page.__file__).resolve().parent.parent / "templates")

_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)
_INPUT = re.compile(r"<input\b([^>]*)>")
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_BUTTON = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S)
_ORIGIN_INPUT = re.compile(
    r'<input type="hidden" name="%s"[^>]*>' % re.escape(actions_page.ORIGIN_FIELD))
_ARTICLE = "<article"


# ------------------------------------------------------------------------------- small readers

def _squeeze(html: str) -> str:
    """*html* with every run of whitespace collapsed — markup and text, never bytes."""
    return re.sub(r"\s+", " ", html).strip()


def _wrapper(page: str, target: str) -> str:
    """The INNER html of one swap wrapper of a rendered page (nested divs walked, not guessed)."""
    match = re.search(r'<div id="%s"[^>]*>' % re.escape(target), page)
    assert match, f"the page draws no #{target} wrapper"
    body = page[match.end():]
    depth = 1
    for tag in re.finditer(r"<(/?)div\b[^>]*>", body):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return body[:tag.start()]
    raise AssertionError(f"#{target} is not closed")


def _player(html: str, name: str) -> str:
    """The `<article>` of ONE radio, keyed on the card's own name element."""
    for chunk in html.split(_ARTICLE)[1:]:
        body = chunk.split("</article>")[0]
        match = re.search(r'<b class="pl-name">(.*?)</b>', body, re.S)
        if match and _squeeze(match.group(1)) == name:
            return _ARTICLE + body + "</article>"
    raise AssertionError(f"no player for {name!r} in the fragment")


def _controls(html: str) -> list[dict]:
    """The transport forms of *html*: the attributes, the hidden fields and the button."""
    found: list[dict] = []
    for attrs, body in _FORM.findall(html):
        attributes = dict(_ATTR.findall(attrs))
        if attributes.get("action") != actions_page.MUSIC_CONTROL_PATH:
            continue
        fields = {}
        for raw in _INPUT.finditer(body):
            named = dict(_ATTR.findall(raw.group(1)))
            if named.get("name"):
                fields[named["name"]] = named.get("value", "")
        # ``raw`` is the button's own attribute text: a bare ``disabled`` carries no value, so the
        # parsed map cannot see it and the pin must read the attributes as written.
        buttons = [dict(_ATTR.findall(raw.group(1)), body=raw.group(2), raw=raw.group(1))
                   for raw in _BUTTON.finditer(body)]
        assert len(buttons) == 1, f"a transport form carries {len(buttons)} buttons"
        found.append({"fields": fields, "button": buttons[0]})
    return found


def _verb(control: dict) -> str:
    return control["fields"].get(actions_page.MUSIC_CONTROL_FIELD, "")


def _disabled(html: str) -> dict[str, bool]:
    """Whether each transport verb is drawn disabled."""
    return {_verb(control): "disabled" in control["button"]["raw"] for control in _controls(html)}


async def _fragments(http, server_name=SERVER_A) -> dict:
    """The polling fallback's answer — the player region exactly as the stream renders it."""
    response = await http.get(detail_page.music_tab_fragments_path(server_name),
                              headers={"accept": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()


async def _open_tab(http, server_name=SERVER_A) -> str:
    return await open_page(http, f"/fleet/{server_name}?tab=music")


def _app(config_dir, cluster=None, users=None, dashboard=None):
    """A real application around *cluster*, with the music plugin and an optional live block.

    The live block is set on the config the shell stored, so the switch is the console's own
    (``pages/live.dashboard_config``) and nothing else about the install is replaced.
    """
    from test_webui_write_pause import _users

    app = build(config_dir, cluster or fleet(), plugins=("mission", "music"),
                users=users if users is not None else _users())
    config = getattr(app.state, "webui_config", None)
    if isinstance(config, dict):
        config["dashboard"] = dict(dashboard or {})
    else:
        app.state.webui_config = {"dashboard": dict(dashboard or {})}
    return app


class SkippingMusic(FakeMusicService):
    """The tab's double, with a skip that MOVES the radio to its next track."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.next_track = {"title": "Welcome to the Jungle", "artist": "Guns N' Roses",
                           "album": "Appetite", "file": "srs-alpha/02.mp3", "duration": 271.0}

    def _entry(self, server_obj, radio_name):
        for entry in self._radios.get(server_obj.name, []):
            if entry["name"] == radio_name:
                return entry
        return None

    async def skip_radio(self, server_obj, radio_name):
        self.controls.append(("skip", radio_name))
        entry = self._entry(server_obj, radio_name)
        if entry is not None:
            entry.update({"on_air": True, "current": dict(self.next_track)})
        return ""


@pytest.fixture
def skipping_music(music_service):
    """Replace the registered double with one whose skip changes the track."""
    from core.services.registry import ServiceRegistry
    from services.music.service import MusicService

    service = SkippingMusic({SERVER_A: [radio(on_air=True)]})
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved


# --------------------------------------------------- 1. one player, drawn by both surfaces

@pytest.mark.asyncio
async def test_both_surfaces_draw_the_one_player_partial(config_dir, music_registry, music_service):
    """The overview's card and the tab's card ARE one template, from one builder.

    Both pages are rendered through the real app from the SAME fixture (one service double, one
    radio), the two `<article>`s are taken out of their wrappers and compared to the character. The
    only per-surface field is the transport's origin — the page a write returns to — so it is dropped
    from the overview's card before the comparison.
    """
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        overview = await open_page(http, music_page.MUSIC_PATH)
        tab = await _open_tab(http)

    from_overview = _player(_wrapper(overview, music_page.RADIOS_TARGET), "srs-alpha")
    from_tab = _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha")

    assert PLAYER_MARKER in from_overview and PLAYER_MARKER in from_tab, \
        "the marker the shared partial carries is on both surfaces"
    assert _ORIGIN_INPUT.search(from_overview), "the overview prescribes its own origin"
    assert not _ORIGIN_INPUT.search(from_tab), "the tab IS the write's default destination"
    assert _squeeze(_ORIGIN_INPUT.sub("", from_overview)) == _squeeze(from_tab), \
        "the two surfaces drew two different players"


def test_the_partial_is_the_only_place_the_card_lives():
    """Neither surface carries a card of its own: both include the ONE partial."""
    for name, label in (("music_body.html", "the Music tab"),
                        ("_music_radios.html", "the overview")):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert PLAYER in source, f"{label} does not include the shared player"
        assert "pl-name" not in source, f"{label} draws a player of its own"


# ------------------------------------------------------------------ 2. glyphs, not word buttons

@pytest.mark.asyncio
async def test_the_transport_is_four_glyph_buttons_with_a_title_and_a_name(
        config_dir, music_registry, music_service):
    """Play, stop, skip and the mode toggle: one glyph button each, named word in the attributes."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        tab = await _open_tab(http)

    card = _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    controls = _controls(card)
    assert [_verb(control) for control in controls] == ["play", "stop", "skip", "mode"], controls
    for control in controls:
        button = control["button"]
        assert "pl-btn" in button.get("class", ""), f"a word button survived: {button}"
        assert "<svg" in button["body"], f"a transport button draws no glyph: {button}"
        assert button.get("aria-label"), f"a glyph button has no accessible name: {button}"
        assert button.get("title"), f"a glyph button has no title: {button}"
    # THE MODE TOGGLE DRAWS THE MODE IN FORCE and posts the other one
    mode = [control for control in controls if _verb(control) == "mode"][0]
    assert mode["fields"][actions_page.MUSIC_MODE_FIELD] == "shuffle", mode["fields"]
    assert mode["button"]["title"] == music_page.MODE_TO_SHUFFLE, mode["button"]["title"]


@pytest.mark.asyncio
async def test_play_is_disabled_on_air_and_stop_and_skip_are_disabled_idle(
        config_dir, music_registry, music_service):
    """Each verb's state is drawn disabled with the sentence that says why — never hidden."""
    music_service._radios[SERVER_A] = [radio(on_air=True)]
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        on_air = _player(_wrapper(await _open_tab(http), detail_page.MUSIC_TAB_TARGET), "srs-alpha")

    assert _disabled(on_air) == {"play": True, "stop": False, "skip": False, "mode": False}
    why = [control["button"]["title"] for control in _controls(on_air) if _verb(control) == "play"]
    assert why == [music_page.ALREADY_ON_AIR], why

    idle_app = _app(config_dir)
    music_service._radios[SERVER_A] = [radio(on_air=False)]
    async with client(idle_app) as http:
        await sign_in(http, "mod1")
        idle = _player(_wrapper(await _open_tab(http), detail_page.MUSIC_TAB_TARGET), "srs-alpha")

    assert _disabled(idle) == {"play": False, "stop": True, "skip": True, "mode": False}
    for verb in ("stop", "skip"):
        titles = [control["button"]["title"] for control in _controls(idle)
                  if _verb(control) == verb]
        assert titles == [music_page.NOT_ON_AIR], titles
    # an idle radio draws no track line and states its one sentence instead
    assert music_page.NO_CURRENT_TRACK in idle


@pytest.mark.asyncio
async def test_a_reader_without_music_control_gets_no_transport_at_all(
        config_dir, music_registry, music_service, monkeypatch):
    """Omitted, never drawn disabled: the player is there, the strip is not."""
    app = _app(config_dir)
    monkeypatch.setitem(permissions.CAPABILITIES, actions_page.MUSIC_CONTROL_CAPABILITY, ("Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, actions_page.MUSIC_CONTROL_CAPABILITY, False)
    async with client(app) as http:
        await sign_in(http, "mod1")                    # DCS Admin: reads, may not control
        tab = await _open_tab(http)

    card = _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    assert "srs-alpha" in card, "the player is still drawn for a reader"
    assert actions_page.MUSIC_CONTROL_PATH not in card, "a transport was drawn for a reader"
    assert "pl-btn" not in tab, "a glyph button was drawn for a reader"
    assert "disabled" not in card, "a withheld control is absent, never drawn disabled"


# ------------------------------------------------------------------ 3. no progress, no pause

@pytest.mark.asyncio
async def test_no_pause_verb_and_no_progress_element(config_dir, music_registry, music_service):
    """Stop IS the pause, and the radio read carries no position — so neither is drawn."""
    music_service._radios[SERVER_A] = [radio(on_air=True)]
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        tab = await _open_tab(http)
        fragments = await _fragments(http)

    for html in (tab, fragments[detail_page.MUSIC_TAB_TARGET]):
        card = _player(html, "srs-alpha")
        assert "pause" not in [_verb(control) for control in _controls(card)]
        assert 'value="pause"' not in card
        assert "Pause" not in card
        for marker in ("<progress", 'role="progressbar"', "pl-progress", "pl-elapsed"):
            assert marker not in card, f"a progress element was drawn: {marker}"
    # the duration IS drawn (it is the one measured figure) on the track's own line
    card = _player(_wrapper(tab, detail_page.MUSIC_TAB_TARGET), "srs-alpha")
    assert 'class="pl-dur' in card, "the track length is the figure the read carries"


# ------------------------------------------------------------------ 4. the tab is live

def test_the_two_stream_paths_carry_music_view(config_dir):
    """Declared like every other route, under the music area's own read capability."""
    app = _app(config_dir)
    registrar = app.state.webui_registrar
    for path in (detail_page.MUSIC_TAB_STREAM_PATH, detail_page.MUSIC_TAB_FRAGMENTS_PATH):
        assert registrar.capability_for(path=path) == music_page.CAPABILITY_VIEW, path
        assert registrar.capability_for(path=path) != permissions.PUBLIC, path
    assert permissions.undeclared_routes(app, registrar) == []


@pytest.mark.asyncio
async def test_a_caller_without_music_view_is_refused_before_any_stream(
        config_dir, music_registry, music_service):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "plain")                   # DCS: no music capability
        for path in (detail_page.music_tab_stream_path(SERVER_A),
                     detail_page.music_tab_fragments_path(SERVER_A)):
            response = await http.get(path, headers={"accept": "text/event-stream"})
            assert response.status_code == 403, path
            assert response.headers.get("location") is None, path


@pytest.mark.asyncio
async def test_a_server_outside_the_caller_s_scope_is_refused(config_dir, music_registry,
                                                             music_service):
    """The SECOND gate: a caller who holds ``music.view`` still cannot stream a foreign server."""
    music_service._radios[SERVER_B] = [radio("srs-b")]
    _declare_hoster(config_dir)
    app = _app(config_dir, users=_hoster_users())
    async with client(app) as http:
        await sign_in(http, "hoster")
        own = await http.get(detail_page.music_tab_fragments_path(SERVER_A),
                             headers={"accept": "application/json"})
        refused = await http.get(detail_page.music_tab_fragments_path(SERVER_B),
                                 headers={"accept": "application/json"})
        stream = await http.get(detail_page.music_tab_stream_path(SERVER_B),
                                headers={"accept": "text/event-stream"})

    assert own.status_code == 200, own.text
    assert refused.status_code == 403 and "outside your scope" in refused.text, refused.text
    assert stream.status_code == 403, stream.text


def test_the_generator_refuses_before_it_exists(config_dir, music_registry, music_service):
    """A refusal inside the body would arrive after the headers — so it is raised HERE."""
    from fastapi import HTTPException

    app = _app(config_dir)
    with pytest.raises(HTTPException) as raised:
        detail_page.music_tab_event_stream(_request(app, subject="plain"), SERVER_A)
    assert raised.value.status_code == 403
    with pytest.raises(HTTPException) as raised:
        detail_page.music_tab_event_stream(_request(app, subject="mod1"), SERVER_B)
    assert raised.value.status_code == 403, "a server outside the scope is refused"

    stream = detail_page.music_tab_event_stream(_request(app, subject="mod1"), SERVER_A)
    assert hasattr(stream, "__anext__"), "a permitted caller receives a real generator"


@pytest.mark.asyncio
async def test_the_stream_opens_with_a_snapshot_and_then_only_changed_frames(
        config_dir, music_registry, skipping_music):
    """A snapshot on connect, then nothing until the radio's track moves — then ONE frame."""
    app = _app(config_dir)
    frames = live_page.stream_frames(
        render=detail_page.music_tab_render_for(_request(app), SERVER_A),
        config=music_page.music_live_config(app),
        registry=live_page.LiveRegistry(4),
        sleep=_scripted_sleep([lambda: None,      # nothing changed -> nothing on the wire
                               _skip,             # the track moved -> one frame
                               lambda: None]),    # nothing changed again -> nothing
        monotonic=lambda: 0.0)

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot"), out[0][:80]
    assert f'"{detail_page.MUSIC_TAB_TARGET}"' in out[0]
    assert len(out) == 2, out
    assert "Welcome to the Jungle" in out[1]
    assert "Fortunate Son" not in out[1], "only the changed region is on the wire"


@pytest.mark.asyncio
async def test_a_skip_on_the_tab_moves_the_next_fragment_to_the_new_track(
        config_dir, music_registry, skipping_music, clean_registry):
    """The reported symptom: press Skip on the tab and the card follows WITHOUT a reload."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        before = await _fragments(http)
        page = await _open_tab(http)
        skip = [control for control in _controls(
            _player(_wrapper(page, detail_page.MUSIC_TAB_TARGET), "srs-alpha"))
            if _verb(control) == "skip"][0]
        posted = dict(skip["fields"])
        posted.pop("action", None)
        reply = await http.post(actions_page.MUSIC_CONTROL_PATH, data=posted,
                                follow_redirects=False)
        after = await _fragments(http)

    assert "Fortunate Son" in before[detail_page.MUSIC_TAB_TARGET]
    assert reply.status_code == 303, reply.text
    assert skipping_music.controls == [("skip", "srs-alpha")], skipping_music.controls
    region = after[detail_page.MUSIC_TAB_TARGET]
    assert "Welcome to the Jungle" in region, "the new track is in the next fragment"
    assert "Fortunate Son" not in region, "the old track is gone from the next fragment"


@pytest.mark.asyncio
async def test_the_tab_draws_the_very_region_the_fragment_carries(
        config_dir, music_registry, music_service):
    """One render path: the page's player region IS the streamed fragment, to the character."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await _open_tab(http)
        fragments = await _fragments(http)

    assert _squeeze(_wrapper(page, detail_page.MUSIC_TAB_TARGET)) == \
        _squeeze(fragments[detail_page.MUSIC_TAB_TARGET])


@pytest.mark.asyncio
async def test_the_tab_is_live_only_for_a_viewer_who_may_stream_it(
        config_dir, music_registry, music_service):
    """The script is loaded on the music tab, and only there — off with the console's switch."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        tab = await _open_tab(http)
        other = await open_page(http, f"/fleet/{SERVER_A}")

    assert "/static/shell.js" in tab
    assert f'data-stream="{detail_page.music_tab_stream_path(SERVER_A)}"' in tab
    assert f'data-fragments="{detail_page.music_tab_fragments_path(SERVER_A)}"' in tab
    assert "/static/shell.js" not in other, "a tab with no live region opens no stream"

    off = _app(config_dir, dashboard={"live": False})
    async with client(off) as http:
        await sign_in(http, "mod1")
        still = await _open_tab(http)

    assert "/static/shell.js" not in still, "dashboard.live: false must open no stream"
    assert "srs-alpha" in still, "the tab is still complete without it"


# --------------------------------------------------------------- 5. New Playlist in accent

@pytest.mark.asyncio
async def test_new_playlist_is_the_shared_accent_button(config_dir, music_registry, music_service):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, music_page.MUSIC_PATH)

    playlists = _wrapper(page, music_page.PLAYLISTS_TARGET)
    band = re.search(r'<div class="hd">(.*?)</div>\s*<div class="tbl">', playlists, re.S)
    assert band, "the playlists card draws its header band"
    assert f'action="{music_page.NEW_DIALOG_PATH}"' in band.group(1)
    button = re.search(r"<button\b([^>]*)>(.*?)</button>", band.group(1), re.S)
    assert button, "the header carries no button"
    assert set(dict(_ATTR.findall(button.group(1))).get("class", "").split()) == \
        {"btn", "primary"}, button.group(1)
    assert token_of(page), "the button still posts the session's token"


@pytest.mark.asyncio
async def test_new_playlist_stays_behind_the_write_capability(config_dir, music_registry,
                                                             music_service):
    from test_webui_music_live import _grant_view_to_media, _users_with_media

    _grant_view_to_media(config_dir)
    app = _app(config_dir, users=_users_with_media())
    async with client(app) as http:
        await sign_in(http, "mm1")
        page = await open_page(http, music_page.MUSIC_PATH)

    playlists = _wrapper(page, music_page.PLAYLISTS_TARGET)
    assert "Rock" in playlists, "the table is still drawn for a reader"
    assert music_page.NEW_DIALOG_PATH not in page, "the door is withheld from a reader"
    assert "btn primary" not in playlists, "an accent button with no write behind it"


# -------------------------------------------------------------------------------- the harness

def _request(app, *, subject: str = "mod1", path: str = ""):
    """A Request carrying the local backend's identity reference — what a session holds."""
    from starlette.requests import Request

    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path or detail_page.MUSIC_TAB_STREAM_PATH, "raw_path": b"", "query_string": b"",
        "root_path": "", "app": app,
        "session": {SESSION_IDENTITY_KEY: {"backend": "local", "subject": subject}},
        "headers": [(b"accept", b"application/json")],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _skip():
    """Move the registered double's track — the change a live frame must carry."""
    from core.services.registry import ServiceRegistry
    from services.music.service import MusicService

    service = ServiceRegistry._singletons[MusicService]
    for entry in service._radios[SERVER_A]:
        entry.update({"on_air": True, "current": dict(service.next_track)})


class _StopTicks(Exception):
    """Raised by a scripted sleep to end the stream after N ticks, with no timing involved."""


def _scripted_sleep(actions):
    """A ``sleep`` whose i-th call runs ``actions[i]`` and raises after the last one."""
    state = {"n": 0}

    async def sleep(_seconds):
        n = state["n"]
        if n >= len(actions):
            raise _StopTicks()
        state["n"] += 1
        actions[n]()

    return sleep


async def _drive(frames) -> list[str]:
    """Every frame a generator yields, stopping at the scripted end of the ticks."""
    out: list[str] = []
    try:
        while True:
            out.append(await anext(frames))
    except (_StopTicks, StopAsyncIteration):
        pass
    return out
