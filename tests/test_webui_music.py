"""The music pages: the playlist list, the playlist editor, and the shared row-drag write.

One section per acceptance criterion of the card, all of it driven THROUGH the application (a request,
not a template render): the capability gate on both pages, the overview's three sections, the editor's
two panes and its folder rail, the reorder POST in both directions (a permutation is one write, a
non-permutation is refused and changes nothing), the CSRF gate on that write, the read-only render of
an action registry that carries nothing, the draft that is never written, and the single-step move
route that writes the same whole order the drag does.

The domain boundary is a ``MusicService`` double registered under the real service class, so the real
action layer runs against a service that answers facts.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import core.actions as actions_module
from core.actions import action_registry, reset_in_flight
from core.services.registry import ServiceRegistry
from services.music.service import MusicService
from services.webservice.pages import music as music_page

# the write suites' fixtures and harness, reused so these pages run through the REAL app
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    PASSWORD, build, client, config_dir, fleet, sign_in, token_of,
)

PLAYLIST = "Jungle Beats"
OTHER = "Awacs Ambient"


# --------------------------------------------------------------------------- the service double

def _track(song_id: int, file: str, *, title: str, artist: str = "", album: str = "",
           duration: float = 240.0, present: bool = True) -> dict:
    return {"song_id": song_id, "file": file, "present": present, "title": title,
            "artist": artist, "album": album, "duration": duration,
            "size": 4_000_000 if present else 0}


class FakeMusic:
    """A ``MusicService`` that answers facts and records every write it is asked to perform."""

    def __init__(self):
        self.playlists = {
            PLAYLIST: [_track(1, "jungle/a.mp3", title="Welcome to the Jungle", artist="Guns N' Roses"),
                       _track(2, "jungle/b.mp3", title="Paint It Black",
                              artist="The Rolling Stones"),
                       _track(3, "jungle/c.mp3", title="Gone", present=False)],
            OTHER: [_track(10, "ambient/a.ogg", title="Dawn Patrol", artist="Brian Eno")],
        }
        self.usage = [{"playlist": PLAYLIST, "server": "SRS-1", "radio": "Radio 1"}]
        self.folders = [
            {"path": "", "name": "music", "depth": 0, "count": 3, "seconds": 720.0,
             "files": [{"title": "Welcome to the Jungle", "artist": "Guns N' Roses", "album": "",
                        "name": "a.mp3", "path": "jungle/a.mp3", "duration": 241.0}]},
            {"path": "jungle", "name": "jungle", "depth": 1, "count": 3, "seconds": 720.0,
             "files": [
                 {"title": "Welcome to the Jungle", "artist": "Guns N' Roses", "album": "Appetite",
                  "name": "a.mp3", "path": "jungle/a.mp3", "duration": 241.0},
                 {"title": "Paint It Black", "artist": "The Rolling Stones", "album": "",
                  "name": "b.mp3", "path": "jungle/b.mp3", "duration": 226.0},
                 {"title": "Fortunate Son", "artist": "Creedence", "album": "",
                  "name": "c.mp3", "path": "jungle/c.mp3", "duration": 140.0}]},
        ]
        self.radios = {
            "SRS-1": [{"name": "Radio 1", "type": "srs", "frequency": "30.0", "modulation": "FM",
                       "coalition": "2", "volume": "0.5", "display_name": "Radio 1", "mode": "repeat",
                       "on_air": True, "playlist": PLAYLIST, "tracks": 3,
                       "current": {"title": "Paint It Black", "artist": "The Rolling Stones",
                                   "album": "", "file": "jungle/b.mp3", "duration": 226.0}}],
        }
        self.calls: list[tuple[str, tuple]] = []

    # ── the reads ─────────────────────────────────────────────────────────
    async def playlist_usage(self):
        return list(self.usage)

    async def playlist_exists(self, name):
        return name in self.playlists

    async def list_playlists(self, node, refresh=False):
        return [{"name": name, "tracks": len(tracks),
                 "seconds": sum(t["duration"] for t in tracks),
                 "missing": len([t for t in tracks if not t["present"]])}
                for name, tracks in sorted(self.playlists.items())]

    async def get_playlist(self, node, name, refresh=False):
        tracks = self.playlists.get(name)
        if tracks is None:
            return None
        return {"name": name, "tracks": list(tracks),
                "seconds": sum(t["duration"] for t in tracks),
                "missing": len([t for t in tracks if not t["present"]])}

    async def read_library(self, node, refresh=False):
        return {"node": getattr(node, "name", ""), "scanned": 1_790_000_000.0,
                "folders": self.folders, "files": 3,
                "seconds": sum(folder["seconds"] for folder in self.folders)}

    async def read_radios(self, server, refresh=False):
        return list(self.radios.get(getattr(server, "name", server), []))

    # ── the writes ────────────────────────────────────────────────────────
    async def add_tracks(self, playlist, files):
        self.calls.append(("add", (playlist, tuple(files))))
        created = playlist not in self.playlists
        self.playlists.setdefault(playlist, [])
        for index, file in enumerate(files, start=100):
            self.playlists[playlist].append(_track(index, file, title=file))
        return {"status": "ok", "playlist": playlist, "added": len(files),
                "tracks": len(self.playlists[playlist]), "created": created}

    async def remove_tracks(self, playlist, song_ids):
        self.calls.append(("remove", (playlist, tuple(song_ids))))
        tracks = self.playlists.get(playlist)
        if tracks is None:
            return {"status": "missing", "playlist": playlist}
        keep = [track for track in tracks if track["song_id"] not in song_ids]
        removed = len(tracks) - len(keep)
        self.playlists[playlist] = keep
        return {"status": "ok", "playlist": playlist, "removed": removed, "tracks": len(keep),
                "unmapped": 0}

    async def reorder_tracks(self, playlist, order):
        self.calls.append(("reorder", (playlist, tuple(order))))
        tracks = self.playlists.get(playlist)
        if tracks is None:
            return {"status": "missing", "playlist": playlist}
        current = [track["song_id"] for track in tracks]
        if sorted(order) != sorted(current) or len(set(order)) != len(order):
            return {"status": "not-a-permutation", "playlist": playlist, "tracks": len(current)}
        by_id = {track["song_id"]: track for track in tracks}
        self.playlists[playlist] = [by_id[song_id] for song_id in order]
        # ``song_ids`` is what the REAL service answers after a write: the rows' ids in playback
        # order. The double renames nothing, so the ids are the posted order — the JSON answer is
        # asserted against this list, which is what lets the page re-stamp its rows.
        return {"status": "ok", "playlist": playlist, "tracks": len(order),
                "song_ids": [int(song_id) for song_id in order]}

    async def rename_playlist(self, playlist, name):
        self.calls.append(("rename", (playlist, name)))
        if playlist not in self.playlists:
            return {"status": "missing", "playlist": playlist}
        if name in self.playlists:
            return {"status": "exists", "playlist": playlist}
        self.playlists[name] = self.playlists.pop(playlist)
        return {"status": "ok", "playlist": name, "previous": playlist,
                "tracks": len(self.playlists[name]), "radios": 1}

    async def delete_playlist(self, playlist):
        self.calls.append(("delete", (playlist,)))
        tracks = self.playlists.pop(playlist, None)
        if tracks is None:
            return {"status": "missing", "playlist": playlist}
        return {"status": "ok", "playlist": playlist, "tracks": len(tracks), "unmapped": 0}

    async def assign_playlist(self, server, radio_name, playlist):
        self.calls.append(("assign", (getattr(server, "name", server), radio_name, playlist)))
        return ""

    async def start_radio(self, server, radio_name):
        self.calls.append(("start", (getattr(server, "name", server), radio_name)))
        return ""

    async def stop_radio(self, server, radio_name):
        self.calls.append(("stop", (getattr(server, "name", server), radio_name)))
        return ""

    async def skip_radio(self, server, radio_name):
        return ""

    async def set_mode(self, server, radio_name, mode):
        return ""


@pytest.fixture
def music_registry():
    """The ``@action`` registry is PROCESS state: clear it and re-import the module for this test."""
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    reset_in_flight()
    sys.modules.pop("plugins.music.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        reset_in_flight()
        sys.modules.pop("plugins.music.actions", None)


@pytest.fixture
def music_service():
    """Register the double under the real service class for this test only."""
    service = FakeMusic()
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved


def app_with(config_dir, *, plugins=("music",)):
    """The real application with a stub cluster behind the app's source provider."""
    return build(config_dir, fleet(), plugins=plugins)


async def open_music(http, path=music_page.MUSIC_PATH):
    """GET one music page as the signed-in caller and return its HTML."""
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def form_token(html: str) -> str:
    """The session's CSRF token, read out of a rendered form."""
    return token_of(html)


# --------------------------------------------------------------------------- 1. the gate

@pytest.mark.asyncio
async def test_a_viewer_with_music_view_reads_both_pages_and_a_member_without_is_refused(
        config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")                          # DCS Admin → music.view
        assert (await http.get(music_page.MUSIC_PATH)).status_code == 200
        assert (await http.get(music_page.playlist_url(PLAYLIST))).status_code == 200

    async with client(app) as http:
        await sign_in(http, "plain")                         # DCS only → no music capability
        assert (await http.get(music_page.MUSIC_PATH)).status_code == 403
        assert (await http.get(music_page.playlist_url(PLAYLIST))).status_code == 403


# --------------------------------------------------------------------------- 2. the overview

@pytest.mark.asyncio
async def test_the_overview_renders_the_on_air_radio_the_playlists_and_the_library(
        config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http)

        # the on-air card: the radio, the current track and its transport summary
        assert "Radio 1" in html
        assert "Paint It Black" in html
        assert "30.0" in html and "FM" in html
        # the playlist table: the name is the door into the editor, its figures and its state
        assert f'href="{music_page.playlist_url(PLAYLIST)}"' in html
        assert "Jungle Beats" in html
        assert "on air" in html
        # the row controls are dialogs, not writes: play and delete post to their dialog paths, and
        # rename lives on the editor page alone — the row carries ONE door, its name and edit glyph
        assert f'action="{music_page.dialog_url(PLAYLIST, "delete")}"' in html
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' in html
        assert f'action="{music_page.dialog_url(PLAYLIST, "rename")}"' not in html
        # the library summary per node: the node's own name and the tree's track count, never the
        # node's disk path (that is not part of the read's payload — see the action pins)
        library = html[html.index(music_page.LIBRARY_HEADING):]
        assert "dcs-a-01" in library
        assert ">3<" in library
        assert 'class="mono">/' not in library


# --------------------------------------------------------------------------- 3. the editor

@pytest.mark.asyncio
async def test_the_editor_renders_the_browser_pane_the_playlist_pane_and_the_folder_rail(
        config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))

        # LEFT: the library card with the folder rail inside it and the selected folder's files
        assert f'action="{music_page.add_url(PLAYLIST)}"' in html
        assert f'href="{music_page.playlist_url(PLAYLIST)}?folder=jungle"' in html
        assert "Welcome to the Jungle" in html
        # RIGHT: the playlist's rows in order, with the focusable grip and the drag attributes
        assert "Welcome to the Jungle" in html
        assert f'data-reorder-url="{music_page.reorder_url(PLAYLIST)}"' in html
        assert 'data-reorder-glyphs' not in html, "the drag is driven by data-reorder alone"
        assert 'data-reorder-handle tabindex="0"' in html, "the grip is the keyboard route"
        assert f'action="{music_page.move_url(PLAYLIST)}"' not in html, "no up/down glyph pair"
        assert f'action="{music_page.remove_url(PLAYLIST)}"' in html
        # the missing flag and the toolbar control that removes them
        assert "missing" in html
        assert f'action="{music_page.remove_url(PLAYLIST)}"' in html
        # the header's dialogs
        assert f'action="{music_page.dialog_url(PLAYLIST, "duplicate")}"' in html
        assert f'action="{music_page.dialog_url(PLAYLIST)}"' in html

        # the folder rail selects a folder and lists ITS files
        chosen = await open_music(http, music_page.playlist_url(PLAYLIST) + "?folder=jungle")
        assert "Fortunate Son" in chosen
        assert "Welcome to the Jungle" in chosen


# --------------------------------------------------------------------------- 4./5. the reorder write

@pytest.mark.asyncio
async def test_the_reorder_post_refuses_a_non_permutation_and_writes_nothing(
        config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))
        response = await http.post(music_page.reorder_url(PLAYLIST),
                                   data={"order": ["1", "2", "999"],
                                         "_csrf_token": form_token(html)})
        assert response.status_code == 200
        answer = response.json()
        assert answer["ok"] is False
        assert "not the current track list" in answer["message"]
        assert [track["song_id"] for track in music_service.playlists[PLAYLIST]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_the_reorder_post_accepts_a_permutation_in_one_write(config_dir, music_registry,
                                                                  music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))
        response = await http.post(music_page.reorder_url(PLAYLIST),
                                   data={"order": ["3", "1", "2"],
                                         "_csrf_token": form_token(html)})
        assert response.status_code == 200
        answer = response.json()
        assert answer["ok"] is True
        # THE ROWS ARE RENUMBERED BY THE WRITE, so the answer must hand the ids back in playback
        # order: without them the page's next drop names ids that no longer exist and is refused as
        # a non-permutation (the "cannot drag a second time" report).
        assert answer["song_ids"] == [3, 1, 2], "the answer carries the ids as the write left them"
        assert [track["song_id"] for track in music_service.playlists[PLAYLIST]] == [3, 1, 2]
        writes = [call for call in music_service.calls if call[0] == "reorder"]
        assert len(writes) == 1, "one drop is one write, never one request per moved row"
        assert writes[0][1] == (PLAYLIST, (3, 1, 2))


@pytest.mark.asyncio
async def test_the_reorder_post_needs_the_session_token(config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.post(music_page.reorder_url(PLAYLIST),
                                   data={"order": ["3", "1", "2"]})
        assert response.status_code == 403
        assert [track["song_id"] for track in music_service.playlists[PLAYLIST]] == [1, 2, 3]


# --------------------------------------------------------------------------- 6. the read-only render

@pytest.mark.asyncio
async def test_an_empty_action_registry_renders_read_only(config_dir, music_registry,
                                                          music_service):
    """No music action in this process: the page renders, and no write control is drawn."""
    app = app_with(config_dir, plugins=())
    async with client(app) as http:
        await sign_in(http, "mod1")
        overview = await open_music(http)
        assert music_page.READ_ONLY in overview
        assert music_page.NEW_DIALOG_PATH not in overview
        assert music_page.dialog_url(PLAYLIST, "rename") not in overview

        editor = await open_music(http, music_page.playlist_url(PLAYLIST))
        assert music_page.add_url(PLAYLIST) not in editor
        assert music_page.reorder_url(PLAYLIST) not in editor
        assert music_page.move_url(PLAYLIST) not in editor
        assert 'data-reorder' not in editor


# --------------------------------------------------------------------------- 7. the draft

@pytest.mark.asyncio
async def test_a_missing_playlist_is_a_draft_and_adding_the_first_track_creates_it(
        config_dir, music_registry, music_service):
    app = app_with(config_dir)
    draft_name = "New Beats"
    async with client(app) as http:
        await sign_in(http, "mod1")
        draft = await open_music(http, music_page.playlist_url(draft_name))
        assert "does not exist yet" in draft
        assert not music_service.calls, "opening a draft writes nothing"

        response = await http.post(music_page.add_url(draft_name),
                                   data={music_page.FILE_FIELD: "jungle/a.mp3",
                                         "_csrf_token": form_token(draft)})
        assert response.status_code == 303
        assert response.headers["location"] == music_page.playlist_url(draft_name)
        assert draft_name in music_service.playlists
        assert [call[0] for call in music_service.calls] == ["add"]


# --------------------------------------------------------------------------- 8. the JS-off glyph

@pytest.mark.asyncio
async def test_the_single_step_move_route_writes_the_whole_order(config_dir, music_registry,
                                                                 music_service):
    """One row one place: the route reads the current list, moves the row and posts the whole order.

    No control on the page posts this route any more (the grip is the reorder control); the route
    itself is the single-step shape of the drag's write and keeps working for a caller that names
    one row instead of an order.
    """
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_music(http, music_page.playlist_url(PLAYLIST))
        response = await http.post(music_page.move_url(PLAYLIST),
                                   data={music_page.SONG_FIELD: "1",
                                         music_page.DIRECTION_FIELD: "down",
                                         "_csrf_token": form_token(html)})
        assert response.status_code == 303
        assert response.headers["location"] == music_page.playlist_url(PLAYLIST)
        assert [track["song_id"] for track in music_service.playlists[PLAYLIST]] == [2, 1, 3]
        writes = [call for call in music_service.calls if call[0] == "reorder"]
        assert len(writes) == 1
        assert writes[0][1] == (PLAYLIST, (2, 1, 3))


# --------------------------------------------------------------------------- 9. the dialogs

@pytest.mark.asyncio
async def test_the_rename_dialog_offers_the_name_and_its_write_renames(config_dir, music_registry,
                                                                    music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        dialog = await http.post(music_page.dialog_url(PLAYLIST, "rename"),
                                 data={"_csrf_token": form_token(page)})
        assert dialog.status_code == 200
        assert 'name="name"' in dialog.text
        assert f'value="{PLAYLIST}"' in dialog.text

        token = token_of(dialog.text)
        reply = await http.post(music_page.run_url(PLAYLIST, "rename"),
                                data={"name": "Jungle Two", "_csrf_token": token})
        assert reply.status_code == 303
        assert reply.headers["location"] == music_page.playlist_url("Jungle Two")
        assert "Jungle Two" in music_service.playlists
        assert PLAYLIST not in music_service.playlists


@pytest.mark.asyncio
async def test_the_delete_dialog_warns_before_the_write(config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        dialog = await http.post(music_page.dialog_url(PLAYLIST, "delete"),
                                 data={"_csrf_token": form_token(page)})
        assert dialog.status_code == 200
        assert music_page.DELETE_WARNING in dialog.text
        assert PLAYLIST in music_service.playlists, "opening a dialog writes nothing"


@pytest.mark.asyncio
async def test_adding_a_whole_folder_is_one_write(config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST) + "?folder=jungle")
        reply = await http.post(music_page.add_url(PLAYLIST),
                                data={"all": "1", "folder": "jungle", "node": "dcs-a-01",
                                      "_csrf_token": form_token(page)})
        assert reply.status_code == 303
        adds = [call for call in music_service.calls if call[0] == "add"]
        assert len(adds) == 1, "a folder is one write"
        assert adds[0][1][1] == ("jungle/a.mp3", "jungle/b.mp3", "jungle/c.mp3")


@pytest.mark.asyncio
async def test_play_on_a_radio_assigns_and_starts(config_dir, music_registry, music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        dialog = await http.post(music_page.dialog_url(PLAYLIST),
                                 data={"_csrf_token": form_token(page)})
        assert dialog.status_code == 200
        assert '<option value="SRS-1|Radio 1"' in dialog.text

        reply = await http.post(music_page.run_url(PLAYLIST),
                                data={"radio": "SRS-1|Radio 1",
                                      "_csrf_token": token_of(dialog.text)})
        assert reply.status_code == 303
        assert [call[0] for call in music_service.calls] == ["assign", "start"]
        assert music_service.calls[0][1] == ("SRS-1", "Radio 1", PLAYLIST)


@pytest.mark.asyncio
async def test_new_playlist_is_a_dialog_that_writes_nothing(config_dir, music_registry,
                                                          music_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        overview = await open_music(http)
        dialog = await http.post(music_page.NEW_DIALOG_PATH,
                                 data={"_csrf_token": form_token(overview)})
        assert dialog.status_code == 200
        assert 'name="name"' in dialog.text

        reply = await http.post(music_page.NEW_RUN_PATH,
                                data={"name": "Fresh Beats", "_csrf_token": token_of(dialog.text)})
        assert reply.status_code == 303
        assert reply.headers["location"] == music_page.playlist_url("Fresh Beats")
        assert not music_service.calls, "opening a new playlist writes nothing"
        assert "Fresh Beats" not in music_service.playlists


# --------------------------------------------------------------------------- 10. the names

def test_the_capability_names_are_the_action_modules_own():
    """The page declares the capabilities; the action module words the refusals — one spelling."""
    from plugins.music import actions as music_actions

    assert music_page.CAPABILITY_VIEW == music_actions.CAPABILITY_VIEW
    assert music_page.CAPABILITY_WRITE == music_actions.CAPABILITY_WRITE
    assert music_page.CAPABILITY_CONTROL == music_actions.CAPABILITY_CONTROL

# --------------------------------------------------------------------------- 11. duplicate refuses

@pytest.mark.asyncio
async def test_duplicate_refuses_a_name_another_playlist_already_holds(
        config_dir, music_registry, music_service):
    """A copy posted under a taken name is refused and the target keeps its own tracks."""
    app = app_with(config_dir)
    before = [(track["song_id"], track["file"]) for track in music_service.playlists[OTHER]]
    typed = OTHER.lower()                      # the comparison is casefold, like "new playlist"

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        reply = await http.post(music_page.run_url(PLAYLIST, "duplicate"),
                                data={"name": typed, "_csrf_token": form_token(page)})
        assert reply.status_code == 303
        assert reply.headers["location"] == music_page.playlist_url(PLAYLIST)
        landed = await open_music(http, reply.headers["location"])

    assert "Not duplicated" in landed and "already exists" in landed
    assert typed in landed, "the refusal names the name that was refused"
    assert [(track["song_id"], track["file"])
            for track in music_service.playlists[OTHER]] == before, "the target playlist gains nothing"
    assert not [call for call in music_service.calls if call[0] == "add"], "no write was attempted"


# --------------------------------------------------------------------------- 12. the play scope pin

@pytest.mark.asyncio
async def test_play_through_the_page_refuses_a_server_outside_the_managers_scope(
        config_dir, music_registry, music_service):
    """``mod1`` is scoped to Host A: a crafted play naming Host B's server writes and starts nothing."""
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_music(http, music_page.playlist_url(PLAYLIST))
        assert 'value="SRS-B|' not in page, "the foreign server is not offered at all"
        reply = await http.post(music_page.run_url(PLAYLIST),
                                data={"radio": "SRS-B|Radio B", "_csrf_token": form_token(page)})
        assert reply.status_code == 303
        landed = await open_music(http, reply.headers["location"])

    assert "outside your scope" in landed
    assert music_service.calls == [], "neither the assignment nor the start ran"


# --------------------------------------------------------------------------- 13. the shared classes

def test_the_shared_drag_script_writes_no_music_prefixed_class():
    """``reorder.js`` serves the radios and the missions list: its classes are shared, not music's."""
    static = Path(music_page.__file__).resolve().parent.parent / "static"
    script = (static / "reorder.js").read_text(encoding="utf-8")

    assert 'line.className = "reorder-note"' in script
    assert "mus-" not in script, "a shared script may not carry a music-prefixed class"
    assert ".reorder-note" in (static / "shell.css").read_text(encoding="utf-8")
    assert ".mus-tracks tr.reorder-dragging" in (static / "music.css").read_text(encoding="utf-8")
