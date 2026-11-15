"""The music website's follow-ups: the row's own name, its length, the grip, one door, the link
colour, and the play door that needs a running server.

Each pin drives the LAYER that owns the fact:

* the row's title, its length and the play door are rendered THROUGH the application (a request, not
  a template render), against the page's ``MusicService`` double;
* the tag read is called directly, on files ffmpeg writes into the test's own directory;
* the drag is the SHIPPED ``static/reorder.js`` booted in a DOM stub by ``tests/reorder_harness.js``;
* the playlist name's colour is the stylesheet's own text — a rule no render can prove.

RED before GREEN: every pin fails on the state that preceded it (an empty title cell, ``0:00``, the
up/down pair, the glyph-gated script, the white name, a Play button with nothing to play on).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from core import Status
from services.webservice.pages import music as music_page

# the page's own doubles and fixtures, reused so these pins run through the REAL application
from test_webui_music import (  # noqa: F401 - fixtures are imported on purpose
    PLAYLIST, FakeMusic, _track, music_registry, music_service, open_music,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    Cluster, build, client, config_dir, instance, server, sign_in, token_of,
)

STATIC = Path(music_page.__file__).parent.parent / "static"
ICONS = (STATIC.parent / "templates" / "_icons.html").read_text(encoding="utf-8")
MUSIC_CSS = (STATIC / "music.css").read_text(encoding="utf-8")
REORDER_JS = STATIC / "reorder.js"
HARNESS = Path(__file__).parent / "reorder_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")
requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                     reason="ffmpeg is not installed: no audio file can be written")


def glyph(name: str) -> str:
    """The drawing ``_icons.html`` holds for *name*, as the rendered SVG carries it."""
    found = re.search(r"'%s': '([^']*)'" % re.escape(name), ICONS)
    assert found, f"the glyph {name} is gone from _icons.html"
    return found.group(1)


def cluster(*, status=Status.RUNNING, other_status=None):
    """Two servers the caller sees, both in *status* unless *other_status* names the second."""
    a = server("SRS-1", ["Host A"], status=status)
    b = server("SRS-B", ["Host B"], status=other_status or status, node_name="dcs-b-01",
               instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


def app_with(config_dir, cluster_):
    return build(config_dir, cluster_, plugins=("music",))


def one_playlist(music, tracks):
    """The double, holding exactly one playlist with *tracks* and no library tree."""
    music.playlists = {PLAYLIST: list(tracks)}
    music.folders = []
    return music


# --------------------------------------------------------------- 1./2. the row's name and length

@pytest.mark.asyncio
async def test_an_untagged_row_renders_the_files_name_and_an_unknown_length_renders_a_dash(
        config_dir, music_registry, music_service):
    """A file with no title tag is named after itself; a file with no duration is not 0:00 long."""
    one_playlist(music_service, [
        _track(1, "jungle/untagged.ogg", title="", duration=0.0),
        _track(2, "jungle/titled.mp3", title="Paint It Black", duration=215.0),
    ])
    # the library pane beside it reads the same files: an unknown length there too
    music_service.folders = [{"path": "", "name": "music", "depth": 0, "count": 1,
                              "seconds": 0.0,
                              "files": [{"title": "", "artist": "", "album": "",
                                         "name": "silent.ogg", "path": "jungle/silent.ogg",
                                         "duration": 0.0}]}]
    app = app_with(config_dir, cluster())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))

        assert '<div class="mus-title">untagged</div>' in html, "the file's name, without its suffix"
        assert "untagged.ogg" not in html.split("mus-tracks")[1], "the suffix is not part of the name"
        assert '<div class="mus-title">Paint It Black</div>' in html, "a tagged row keeps its tag"
        assert '<td class="mono">—</td>' in html, "an unknown duration is an em dash"
        assert "0:00" not in html, "an unknown duration is never rendered as zero seconds"
        assert '<td class="mono">3:35</td>' in html, "a known duration is still m:ss"
        library = html[:html.index("mus-tracks")]
        assert '<td class="mono">—</td>' in library, "the library pane renders the dash too"


# --------------------------------------------------------------- 3. the tag reader itself

def _audio(directory: Path, name: str, codec: str, metadata: tuple = ()) -> str:
    """One 7-second sine written by ffmpeg, with the given ``key=value`` metadata."""
    target = directory / name
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", "sine=frequency=440:duration=7"]
    for entry in metadata:
        command += ["-metadata", entry]
    command += ["-c:a", codec, str(target)]
    subprocess.run(command, check=True)
    return str(target)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_read_tags_reads_a_vorbis_and_an_opus_ogg_and_leaves_the_mp3_reader_alone(tmp_path):
    """The .ogg read is the container's own headers: title, artist, album and a real duration."""
    from services.music.tags import read_tags

    tagged = read_tags(_audio(tmp_path, "tagged.ogg", "libvorbis",
                              ("title=Ogg Title", "artist=Ogg Artist", "album=Ogg Album")))
    assert tagged["title"] == "Ogg Title"
    assert tagged["artist"] == "Ogg Artist"
    assert tagged["album"] == "Ogg Album"
    assert tagged["duration"] == pytest.approx(7.0, abs=0.2), "the granule position is the length"

    untagged = read_tags(_audio(tmp_path, "plain.ogg", "libvorbis"))
    assert untagged["title"] == "", "no title tag in the file, no title in the read"
    assert untagged["duration"] == pytest.approx(7.0, abs=0.2)

    opus = read_tags(_audio(tmp_path, "plain.opus.ogg", "libopus"))
    assert opus["duration"] == pytest.approx(7.0, abs=0.2), "an Opus stream is counted at 48 kHz"

    mp3 = read_tags(_audio(tmp_path, "plain.mp3", "libmp3lame"))
    pytest.importorskip("eyed3", reason="eyed3 is not installed: the .mp3 reader cannot run")
    assert mp3["title"] == ""
    assert mp3["duration"] > 0, "an untagged mp3 still knows how long it is"


# --------------------------------------------------------------- 4. drag only, one grip

@pytest.mark.asyncio
async def test_the_editor_draws_one_focusable_grip_and_no_up_down_glyph(
        config_dir, music_registry, music_service):
    one_playlist(music_service, [_track(1, "a.mp3", title="One"), _track(2, "b.mp3", title="Two")])
    app = app_with(config_dir, cluster())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))

        # the grip: one per row, focusable, and it says what it does
        assert html.count("data-reorder-handle") == 2, "one grip per row"
        assert html.count('data-reorder-handle tabindex="0"') == 2, "the grip is a tab stop"
        # the up/down pair is gone; the remove glyph stays
        assert f'action="{music_page.move_url(PLAYLIST)}"' not in html
        assert "Move up" not in html and "Move down" not in html
        assert f'action="{music_page.remove_url(PLAYLIST)}"' in html
        # the drag itself does not depend on the glyph attribute any more
        assert "data-reorder-glyphs" not in html
        assert f'data-reorder-url="{music_page.reorder_url(PLAYLIST)}"' in html


@requires_node
def test_the_shipped_script_posts_one_whole_order_per_gesture_without_the_glyph_attribute():
    """The REAL file, in a DOM stub: a drop and a keypress each post the whole order once."""
    assert HARNESS.is_file(), "the harness that drives the shipped drag script is missing"
    done = subprocess.run(["node", str(HARNESS), str(REORDER_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)

    assert report["glyph_attribute_absent"], "the table opts in with data-reorder alone"
    drop = report["drop"]
    assert drop["requests"] == 1, "one drop is one write"
    assert drop["url"] == "/music/Jungle%20Beats/reorder"
    assert drop["method"] == "POST"
    assert drop["order"] == drop["dom"], "the body carries the WHOLE new order"
    assert drop["order"] == ["2", "1", "3"], "the row moved one place"

    # THE OTHER DIRECTION: dragging row 2 ABOVE row 1 must swap them. A downward-only algorithm
    # passes the drop above and moves nothing upward — "I can push 1 down but not 2 up".
    upward = report["drop_up"]
    assert upward["requests"] == 1, "an upward drag is one write, not none"
    assert upward["dom"] == upward["order"] == ["2", "1", "3"], "row 2 took row 1's place"

    keyboard = report["keyboard_down"]
    assert keyboard["requests"] == 1, "Alt+ArrowDown is the same one write"
    assert keyboard["order"] == keyboard["dom"] == ["2", "1", "3"]
    assert report["keyboard_edge"]["requests"] == 0, "the first row does not move up"
    assert report["no_handle"]["requests"] == 0, "a press off the grip starts no drag"

    refusal = report["refusal"]
    assert refusal["dom"] == ["1", "2", "3"], "a refusal puts the rows back"
    assert refusal["status"] == "Not the current track list.", "the server's own sentence"

    # A LIST THAT NEEDS MORE THAN THE ORDER names its extra params on the element and every request
    # carries them. The missions route cannot resolve its target without the server, and the drop's
    # answer is a redirect rather than JSON when it cannot — the "refreshes and the mission is back"
    # report. The shipped script appends these params to the same body as the order.
    params = report["params"]
    assert params["requests"] == 1, "a keypress is still one write"
    assert params["server"] == ["Alpha"], "the list's own static params travel with the order"
    assert params["order"] == ["2", "1", "3"], "the order travels beside them"

    # A WRITE MAY RENUMBER THE ROWS: the answer's song_ids re-stamp the rows so the NEXT drop names
    # ids that exist. Without this the second drag posts ids the write already replaced and is
    # refused as a non-permutation — "cannot drag anything else until refresh".
    assert report["restamp"]["dom"] == ["9", "8", "7"], "the answer's ids replace the stale ones"


# --------------------------------------------------------------- 5. one door per playlist

@pytest.mark.asyncio
async def test_the_playlist_row_has_one_door_that_names_editing(config_dir, music_registry,
                                                                music_service):
    one_playlist(music_service, [_track(1, "a.mp3", title="One")])
    app = app_with(config_dir, cluster())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http)
        row = html[html.index(f'<a class="mus-name" href="{music_page.playlist_url(PLAYLIST)}"'):]
        row = row[:row.index("</tr>")]

        assert "Edit the playlist &#39;Jungle Beats&#39;" in row or \
               "Edit the playlist 'Jungle Beats'" in row
        pencil = re.search(r'class="act-btn" href="[^"]*" title="[^"]*" aria-label="[^"]*">'
                           r'<svg class="ic24".*?</svg>', row, re.S)
        assert pencil, "the row's door is a glyph link"
        assert glyph("pencil") in pencil.group(0), "the glyph is the pencil"
        assert glyph("view") not in row, "the door is not drawn as `view`"
        # rename lives on the editor page only
        assert f'action="{music_page.dialog_url(PLAYLIST, "rename")}"' not in html
        assert f'action="{music_page.dialog_url(PLAYLIST, "delete")}"' in html


# --------------------------------------------------------------- 6. the name is a link

def test_the_playlist_name_carries_no_fixed_colour():
    block = re.search(r"^\.mus-name \{(?P<body>[^}]*)\}", MUSIC_CSS, re.M)
    assert block, ".mus-name is gone from the stylesheet"
    assert "color" not in block.group("body"), "the name inherits the shell's link colour"
    assert "font-weight" in block.group("body"), "the weight stays the row's"


# --------------------------------------------------------------- 7. play only while running

@pytest.mark.asyncio
async def test_the_play_door_is_absent_while_every_server_is_down_and_stated(
        config_dir, music_registry, music_service):
    one_playlist(music_service, [_track(1, "a.mp3", title="One")])
    app = app_with(config_dir, cluster(status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' not in html, "no dead control"
        assert music_page.PLAY_OFFLINE in html, "the page says why instead"
        # the rest of the page still renders
        assert f'action="{music_page.dialog_url(PLAYLIST, "rename")}"' in html


@pytest.mark.asyncio
async def test_the_play_door_is_drawn_once_a_server_runs(config_dir, music_registry, music_service):
    one_playlist(music_service, [_track(1, "a.mp3", title="One")])
    app = app_with(config_dir, cluster(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' in html
        assert music_page.PLAY_OFFLINE not in html


@pytest.mark.asyncio
async def test_the_overview_play_glyph_is_absent_while_every_server_is_down(
        config_dir, music_registry, music_service):
    """The LIST page's row: with every server down there is no Play glyph to click.

    The editor's own door was already gated; this is the row on the overview page, whose glyph opens
    the same radio dialog. A dead control there was the report "the play glyph is still there, even if
    no server is running".
    """
    one_playlist(music_service, [_track(1, "a.mp3", title="One")])
    app = app_with(config_dir, cluster(status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http)
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' not in html, "no dead Play glyph"


@pytest.mark.asyncio
async def test_the_overview_play_glyph_is_drawn_once_a_server_runs(
        config_dir, music_registry, music_service):
    one_playlist(music_service, [_track(1, "a.mp3", title="One")])
    app = app_with(config_dir, cluster(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http)
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' in html


@pytest.mark.asyncio
async def test_the_play_dialog_offers_only_running_servers(config_dir, music_registry,
                                                           music_service):
    """One server up, one down, one radio each: only the running one is offered."""
    music_service.playlists = {PLAYLIST: [_track(1, "a.mp3", title="One")]}
    music_service.folders = []
    music_service.radios = {
        "SRS-1": [{"name": "Radio 1", "type": "srs", "frequency": "30.0", "modulation": "FM",
                   "coalition": "2", "volume": "0.5", "display_name": "Radio 1", "mode": "repeat",
                   "on_air": False, "playlist": PLAYLIST, "tracks": 1, "current": {}}],
        "SRS-B": [{"name": "Radio B", "type": "srs", "frequency": "40.0", "modulation": "AM",
                   "coalition": "1", "volume": "0.5", "display_name": "Radio B", "mode": "repeat",
                   "on_air": False, "playlist": PLAYLIST, "tracks": 1, "current": {}}],
    }
    app = app_with(config_dir, cluster(status=Status.RUNNING, other_status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "specialk")          # an Admin sees BOTH servers, one of them down
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        answer = await http.post(music_page.dialog_url(PLAYLIST),
                                 data={"_csrf_token": token_of(page)})
        assert answer.status_code == 200, answer.text[:300]
        dialog = answer.text

        assert "Radio 1" in dialog, "a running server's radio is offered"
        assert "Radio B" not in dialog, "a stopped server's radio is not"
