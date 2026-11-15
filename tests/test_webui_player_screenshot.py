"""The PHOTO GLYPH: a screenshot of one player's screen, and the modal it arrives in.

The console's one player control whose answer is an IMAGE. ``screenshot_player``
(``plugins/mission/actions.py``) asks the server for a capture and answers with the KEY of the file
DCS wrote; the website's own route reads that file out of the server's web GUI and keeps it under a
SHORT-LIVED token, which the page the write lands on opens as a modal. Pins: the declaration (its
capability, its glyph, and the SERVER permission that gates it); the row (offered only where the
server allows screenshots, omitted — never disabled — where it does not); the write (the capture is
made, the bytes are read back, the modal carries the token URL); the short life (a capture past its
window is swept and its URL 404s, and the modal is SPENT so a reload does not re-open it); and the
refusals (a server that may not screenshot, and a capture that cannot be read back, both reported in
words rather than shown as a photo). Driven through the real app against the real mission action.
"""
from __future__ import annotations

import importlib
import re
import types

import pytest

from conftest import permissions  # noqa: F401 - re-exported for the harness import below
from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, sign_in, token_of,
)

SHOT = actions_page.SCREENSHOT_PATH
IMAGE = actions_page.SCREENSHOT_IMAGE_PATH
UCID = "ucid-shot-1"
PLAYER = "Fragmagnet"
#: not a real PNG: the console never decodes it, it only carries the bytes to the browser
PNG = b"\x89PNG\r\n\x1a\n" + b"the-captured-frame"


def _mission():
    """``plugins.mission.actions``, imported INSIDE a test — never at collection time (see the
    console suites' own rule: the import populates the ``@action`` registry process-wide)."""
    return importlib.import_module("plugins.mission.actions")


@pytest.fixture(autouse=True)
def _isolated_registry(clean_registry):  # noqa: F811 - the harness fixture, made autouse here
    return clean_registry


@pytest.fixture(autouse=True)
def _no_poll_delay(monkeypatch):
    """The capture wait sleeps between looks; a test does not wait a second per look."""
    monkeypatch.setattr(_mission(), "SCREENSHOT_POLL_SECONDS", 0)


@pytest.fixture(autouse=True)
def _empty_captures():
    """The capture store is PROCESS state (like the awaiting-change store): empty for every test."""
    actions_page._SCREENSHOT_CAPTURES.clear()
    yield
    actions_page._SCREENSHOT_CAPTURES.clear()


class ShotPlayer:
    """The ``Player`` double for a capture: the three methods, and the file list they move."""

    def __init__(self, name, ucid, *, files=(), capture=True):
        self.name = name
        self.display_name = name
        self.ucid = ucid
        self.active = True
        self.screenshots = list(files)
        #: whether asking for one actually produces a file (False = the server never answers)
        self.capture = capture
        self.calls: list[str] = []

    async def getScreenshots(self):
        self.calls.append("list")
        return list(self.screenshots)

    async def makeScreenshot(self):
        self.calls.append("make")
        if self.capture:
            self.screenshots.append("Screen_20261018_120000.png")

    async def deleteScreenshot(self, key):
        self.calls.append("delete")


def shot_server(name="SRS-1", *, can_screenshot=True, slow=False, image=PNG, capture=True,
                webgui_port=8088):
    """A server double shaped like the parts the screenshot path reads: the settings the gate looks
    at, the node's file accessor, and the instance's web GUI port."""
    player = ShotPlayer(PLAYER, UCID, capture=capture)

    async def read_file(url):
        return image

    srv = types.SimpleNamespace(
        name=name, display_name=name, status=Status.RUNNING, port=10309,
        node=types.SimpleNamespace(name="dcs-a-01", locals={"slow_system": slow},
                                   read_file=read_file),
        instance=types.SimpleNamespace(name="Host-A-Instance", webgui_port=webgui_port),
        settings={"advanced": {"server_can_screenshot": can_screenshot}},
        players={UCID: player}, is_remote=False,
        locals={"managed_by": ["Host A"]}, calls=[],
    )
    player.server = srv

    def get_player(ucid=None, active=None):
        """``Server.get_player``'s UCID branch — the only one these actions use."""
        found = srv.players.get(ucid)
        if found is None or (active is not None and found.active != active):
            return None
        return found

    srv.get_player = get_player
    return srv, player


def fleet(*, can_screenshot=True, capture=True, image=PNG, webgui_port=8088):
    srv, player = shot_server(can_screenshot=can_screenshot, capture=capture, image=image,
                              webgui_port=webgui_port)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", srv)})
    return Cluster([srv], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", srv)]), srv, player


async def open_players(http):
    response = await http.get(ROSTER)
    assert response.status_code == 200, response.text
    return response.text


def offered(html: str) -> set[str]:
    """The control paths the page actually RENDERS, for a player row."""
    return set(re.findall(r'action="(/actions/player/[^"]+)"', html))


def modal_of(html: str) -> str:
    """The screenshot modal's own markup, or ``""`` when the page rendered none."""
    match = re.search(r'<div class="shot-back".*?</div>\s*</div>\s*</div>', html, re.S)
    return match.group(0) if match else ""


def image_url_of(html: str) -> str:
    match = re.search(r'<img src="([^"]+)"', modal_of(html))
    assert match, f"the modal carries no image:\n{modal_of(html)}"
    return match.group(1)


# ------------------------------------------------------------------ 1. the declaration

def test_the_screenshot_control_is_declared_gated_and_glyphed():
    """The declaration is asserted where it lives: one glyph, the player-write capability, and the
    SERVER permission that decides whether the server can answer at all."""
    action = next(a for a in actions_page.PLAYER_ACTIONS if a.key == actions_page.SCREENSHOT_KEY)
    assert action.capability == actions_page.SCREENSHOT_CAPABILITY == "players.screenshot"
    assert action.qualname == "screenshot_player"
    assert action.path == SHOT
    assert action.icon == "camera"
    assert action.when_server_allows == actions_page.SCREENSHOT_SETTING, \
        "the glyph must be gated on the server's own permission"
    assert action.when_muted is None, "a screenshot is not a mute-pair control"
    assert action.confirm is False, \
        "the glyph posts straight to its action — there is nothing to confirm"


def test_the_screenshot_paths_and_the_image_url_declare_the_same_capability():
    """Both doors are declared: the glyph's POST and the short-lived image URL it opens."""
    caps = actions_page.capabilities()
    assert caps[SHOT] == actions_page.SCREENSHOT_CAPABILITY
    assert caps[f"{IMAGE}/{{token}}.png"] == actions_page.SCREENSHOT_CAPABILITY, \
        "the image URL must declare the same capability as the glyph that mints it"
    declared = permissions.roles_for_capability(actions_page.SCREENSHOT_CAPABILITY)
    assert declared, "players.screenshot is not declared in the capability table"


def test_the_glyph_that_is_declared_is_one_the_icons_partial_draws():
    from pathlib import Path
    source = Path(actions_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z_]+)':\s*'", source.read_text(encoding="utf-8")))
    assert "camera" in glyphs, sorted(glyphs)


# ------------------------------------------------------------------------- 2. the row

@pytest.mark.asyncio
async def test_the_photo_glyph_is_offered_where_the_server_allows_screenshots(config_dir):
    """``advanced.server_can_screenshot`` decides it: on, the row renders the glyph."""
    cluster, srv, _player = fleet(can_screenshot=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_players(http)
    assert SHOT in offered(html), sorted(offered(html))
    assert 'title="Screenshot — capture this player' in html


@pytest.mark.asyncio
async def test_the_glyph_is_omitted_where_the_server_cannot_screenshot(config_dir):
    """A server that may not screenshot is a server whose glyph is ABSENT — never rendered disabled."""
    cluster, _srv, _player = fleet(can_screenshot=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_players(http)
    assert SHOT not in offered(html), sorted(offered(html))
    assert "Screenshot — capture this player" not in html


# ------------------------------------------------------------------------ 3. the write

@pytest.mark.asyncio
async def test_a_press_takes_the_photo_and_the_page_opens_it(config_dir):
    """ACCEPTANCE: the capture reaches the server, comes back as a modal, and its URL serves the bytes.

    The glyph is a control that stays on its page, so the write's answer is the page it was pressed
    on — and the modal is rendered by the NEXT render of that page, once (see the test below).
    """
    cluster, _srv, player = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        reply = await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        assert reply.status_code == 303, reply.text
        assert "make" in player.calls, "the server was never asked for a capture"
        after = await open_players(http)
        url = image_url_of(after)
        assert url.startswith(f"{IMAGE}/") and url.endswith(".png"), url
        fetched = await http.get(url)
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == PNG, "the URL must serve the bytes the server wrote"
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_the_modal_is_shown_once_and_the_token_is_the_only_handle(config_dir):
    """SPENT on the render that shows it: a reload must not re-open the picture."""
    cluster, _srv, _player = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        first = await open_players(http)
        second = await open_players(http)
    assert '<div class="shot-back"' in first
    assert '<div class="shot-back"' not in second, "the modal must not re-open on a reload"
    assert UCID not in image_url_of(first), "the URL must not name the player — the token is the handle"
    assert "SRS-1" not in image_url_of(first)


@pytest.mark.asyncio
async def test_a_capture_older_than_its_window_is_swept_and_its_url_is_gone(config_dir):
    """The SHORT life: a day, then the bytes are dropped and the URL answers 404."""
    cluster, _srv, _player = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        url = image_url_of(await open_players(http))
        capture = next(iter(actions_page._SCREENSHOT_CAPTURES.values()))
        capture.created -= actions_page.SCREENSHOT_RETENTION_SECONDS + 1
        gone = await http.get(url)
    assert gone.status_code == 404, gone.text
    assert actions_page._SCREENSHOT_CAPTURES == {}, "the swept entry must not be kept"
    assert actions_page.screenshot_capture(capture.token) is None


@pytest.mark.asyncio
async def test_an_unknown_token_is_the_same_answer_as_a_swept_one(config_dir):
    """A probe learns nothing: a token that never existed answers exactly like one past its life."""
    cluster, _srv, _player = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        reply = await http.get(f"{IMAGE}/not-a-token.png")
    assert reply.status_code == 404
    assert "Not a screenshot" in reply.text or "no such" in reply.text.lower()


# --------------------------------------------------------------------- 4. the refusals

@pytest.mark.asyncio
async def test_a_server_that_may_not_screenshot_refuses_a_crafted_post(config_dir):
    """The glyph is omitted there, but a POST that skipped the page still meets the ACTION's refusal."""
    cluster, _srv, player = fleet(can_screenshot=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        reply = await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        assert reply.status_code == 303, reply.text
        after = await open_players(http)
    assert "make" not in player.calls, "no capture may be asked for on a server that cannot take one"
    assert actions_page._SCREENSHOT_CAPTURES == {}, "a refusal stores no image"
    assert "can not take screenshots" in after, "the refusal must be reported in words"


@pytest.mark.asyncio
async def test_a_capture_that_never_appears_is_a_timeout_not_a_photo(config_dir):
    """The wait ends on the timeout: reported in words, and nothing is stored."""
    cluster, _srv, player = fleet(capture=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        reply = await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        after = await open_players(http)
    assert reply.status_code == 303
    assert "make" in player.calls
    assert actions_page._SCREENSHOT_CAPTURES == {}
    assert "Timeout while waiting for the screenshot" in after
    assert '<div class="shot-back"' not in after


@pytest.mark.asyncio
async def test_a_capture_that_cannot_be_read_back_is_reported_not_shown(config_dir):
    """Taken, but the read fails: the answer is a failure, because a success with no image is nothing."""
    cluster, srv, _player = fleet(image=b"")

    async def read_file(url):
        raise OSError("no route to the server's web GUI")

    srv.node.read_file = read_file
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        reply = await http.post(SHOT, data={"server": "SRS-1", "ucid": UCID, "_csrf_token": token})
        after = await open_players(http)
    assert reply.status_code == 303, reply.text
    assert actions_page._SCREENSHOT_CAPTURES == {}
    assert "could not be read back" in after
    assert '<div class="shot-back"' not in after
