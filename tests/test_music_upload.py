"""The library card's UPLOAD: the leaf rule, the folder containment, the write, and the one door.

Every acceptance criterion of the card has a section, and each section drives the LAYER that owns
it: the path rules are called directly, the write runs against a real ``MusicService`` over a real
temp directory (a fake ``node.write_file`` is the only stand-in — that method IS the node's disk),
the action runs against a service double so its own refusals are observable without a file, and the
console's route runs THROUGH the application with a signed-in identity and a stub cluster — once
against the page's ``MusicService`` double, and once against the REAL service over a real directory,
which is the whole door from a multipart POST to the track the next render lists.

The upload's seam that no stub proves is the last line of the card: a real file landing on a REMOTE
node's disk. Everything here writes through ``node.write_file``, which on a remote node is an RPC.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import pytest

import core.actions as actions_module
from core import UploadStatus
from core.actions import action_registry, reset_in_flight
from core.const import DEFAULT_TAG
from core.services.registry import ServiceRegistry
from services.music import service as service_module
from services.music.service import (
    TRACK_EXISTS, TRACK_FOLDER_INVALID, TRACK_FOLDER_MISSING, TRACK_INVALID_NAME, TRACK_NOT_AUDIO,
    TRACK_OK, TRACK_WRITE_ERROR, MusicService, track_suffix, upload_destination, upload_leaf,
)
from services.webservice import permissions
from services.webservice.pages import music as music_page

# the write suites' fixtures and harness, reused so these cases run through the REAL app
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    build, client, config_dir, fleet, recording_bot, sign_in, token_of,
)
from test_webui_music import PLAYLIST, FakeMusic as _PageMusic

TRACK_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
UPLOAD = "Make It Wit Chu.mp3"


# --------------------------------------------------------------------------- the path rules

def test_upload_leaf_accepts_a_name_and_refuses_every_shape_that_is_not_one():
    assert upload_leaf("Make It Wit Chu.mp3") == "Make It Wit Chu.mp3"
    assert upload_leaf("  padded.ogg  ") == "padded.ogg"
    for refused in ("", "   ", None, 7, "..", ".", "../x.mp3", "a/b.mp3", "a\\b.mp3",
                    "C:\\x.mp3", "C:x.mp3", "\\\\host\\share\\x.mp3", "x\x00.mp3", "."):
        assert upload_leaf(refused) is None, refused


def test_upload_leaf_refuses_a_name_that_reorders_what_it_renders():
    """A RIGHT-TO-LEFT OVERRIDE keeps the real extension while the name is DRAWN in another order.

    U+202A-U+202E reorder the rendered name (``x<U+202E>xe.mp3`` is drawn as an ``.exe`` that the
    library would store as audio), and a control character is not a name at all. Ordinary non-ASCII is
    accepted VERBATIM — the name on screen is the name on the disk.
    """
    for refused in ("x\u202Exxe.mp3", "\u202Ax.mp3", "x\u202Ax.mp3", "\u202Bx.mp3", "\u202Cx.mp3",
                    "\u202Dx.mp3", "x.mp3\u202E", "x\x00.mp3", "x\x01.mp3", "x\t.mp3", "x\x1f.mp3",
                    "x\x7f.mp3"):
        assert upload_leaf(refused) is None, repr(refused)
    for kept in ("Björk – Jóga.mp3", "日本語の曲.ogg", "Ça va.mp3", "99 Luftballons.mp3"):
        assert upload_leaf(kept) == kept


def test_track_suffix_is_the_library_pair_and_nothing_else():
    assert track_suffix("a.mp3") == ".mp3"
    assert track_suffix("a.OGG") == ".ogg"
    for refused in ("a.txt", "a.mp3.exe", "a", "a.wav", "a.flac", ".mp3.exe"):
        assert track_suffix(refused) is None, refused


def test_upload_destination_resolves_the_folder_and_folds_its_separators(tmp_path):
    root = str(tmp_path / "music")
    assert upload_destination(root, "", "a.mp3") == (
        os.path.abspath(os.path.join(root, "a.mp3")), "", "a.mp3")
    path, folder, track = upload_destination(root, "jungle", "a.mp3")
    assert path == os.path.abspath(os.path.join(root, "jungle", "a.mp3"))
    assert (folder, track) == ("jungle", "jungle/a.mp3")
    # the client's own separator is folded, not trusted
    assert upload_destination(root, "jungle\\sub", "a.mp3")[1] == "jungle/sub"
    assert upload_destination(root, "./jungle//sub/", "a.ogg")[1] == "jungle/sub"


def test_upload_destination_refuses_a_folder_that_leaves_the_music_directory(tmp_path):
    root = str(tmp_path / "music")
    for refused in ("..", "../etc", "jungle/../../etc", "/etc", "C:/music", "jungle/\x00"):
        assert upload_destination(root, refused, "a.mp3") is None, refused
    # a leaf is the only shape that can be joined: a name with a separator never arrives here
    assert upload_destination(root, "jungle", "../a.mp3") is None


# --------------------------------------------------------------------------- the write on the node

class _Node:
    """The node's own writer: it puts the staged bytes on THIS filesystem, like ``NodeImpl``.

    ``write_file`` is the ONE call the service makes on the disk, so this double is the disk — every
    other method is the import-time registry's business.
    """

    def __init__(self, *, staged: dict | None = None, outcome: UploadStatus = UploadStatus.OK):
        self.name = "dcs-a-01"
        self.master = True
        self.is_remote = False
        self.staged = dict(staged or {})
        self.outcome = outcome
        self.writes: list[tuple[str, int]] = []
        self.attempts: list[tuple[str, object]] = []

    async def write_file(self, target, source, overwrite=False):
        self.attempts.append((target, source))
        if os.path.exists(target) and not overwrite:
            return UploadStatus.FILE_EXISTS
        if self.outcome != UploadStatus.OK:
            return self.outcome
        data = self.staged.pop(source, None)
        if data is None:
            return UploadStatus.READ_ERROR
        with open(target, "wb") as out:
            out.write(data)
        self.writes.append((target, len(data)))
        return UploadStatus.OK


def service(root, node) -> MusicService:
    """A ``MusicService`` over *root* whose writer is *node* (no ``__init__``: no bus, no config file)."""
    built = MusicService.__new__(MusicService)
    built.node = node
    built.log = logging.getLogger("test.music.upload")
    built.locals = {DEFAULT_TAG: {"music_dir": str(root), "radios": {}}}
    built._config = {}
    built.apool = None
    built.pool = None
    built.radios = {}
    built._library_cache = {}
    built._library_generation = {}
    return built


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A music directory with one folder, and a tag reader that costs nothing."""
    root = tmp_path / "music"
    (root / "jungle").mkdir(parents=True)
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": os.path.basename(file), "artist": "", "album": "",
                                      "duration": 10.0, "size": 1})
    return root


def test_a_successful_upload_writes_the_bytes_and_is_in_the_next_scan(library):
    node = _Node(staged={7: TRACK_BYTES})
    built = service(library, node)

    before = asyncio.run(built.read_library(node))
    assert before["files"] == 0

    answer = asyncio.run(built.save_track(node, "jungle", UPLOAD, 7))

    assert answer["status"] == TRACK_OK, answer
    assert (answer["folder"], answer["track"]) == ("jungle", f"jungle/{UPLOAD}")
    assert answer["size"] == len(TRACK_BYTES)
    assert (library / "jungle" / UPLOAD).read_bytes() == TRACK_BYTES
    assert node.writes == [(str(library / "jungle" / UPLOAD), len(TRACK_BYTES))]

    # THE POINT OF THE CARD: the new track is in the library read WITHOUT a manual rescan
    after = asyncio.run(built.read_library(node))
    assert after["files"] == 1, after
    assert after["generation"] == before["generation"] + 1
    folders = {folder["path"]: folder for folder in after["folders"]}
    assert [file["path"] for file in folders["jungle"]["files"]] == [f"jungle/{UPLOAD}"]


def test_an_upload_proposed_over_an_existing_file_is_refused_and_the_file_survives(library):
    node = _Node(staged={8: b"NEW"})
    built = service(library, node)
    (library / "jungle" / UPLOAD).write_bytes(b"OLD")

    answer = asyncio.run(built.save_track(node, "jungle", UPLOAD, 8))

    assert answer["status"] == TRACK_EXISTS
    assert (library / "jungle" / UPLOAD).read_bytes() == b"OLD", "nothing was overwritten"
    assert node.attempts == [], "a taken name is refused BEFORE the writer is reached"
    assert node.staged == {8: b"NEW"}, "the caller's staged row is its own to discard"


def test_the_writers_own_file_exists_answer_is_the_same_refusal(library):
    """A file that appeared between the check and the write is reported, not overwritten."""
    node = _Node(staged={9: b"NEW"}, outcome=UploadStatus.FILE_EXISTS)
    built = service(library, node)
    assert asyncio.run(built.save_track(node, "jungle", UPLOAD, 9))["status"] == TRACK_EXISTS


def test_a_write_error_is_reported_and_does_not_touch_the_generation(library):
    node = _Node(staged={10: b"X"}, outcome=UploadStatus.WRITE_ERROR)
    built = service(library, node)
    before = asyncio.run(built.read_library(node))

    answer = asyncio.run(built.save_track(node, "jungle", UPLOAD, 10))

    assert answer["status"] == TRACK_WRITE_ERROR
    assert asyncio.run(built.read_library(node))["generation"] == before["generation"]


def test_the_service_refuses_its_own_bad_names_an_extension_and_a_foreign_folder(library):
    node = _Node(staged={11: b"X"})
    built = service(library, node)
    cases = (
        ("../x.mp3", "jungle", TRACK_INVALID_NAME),
        ("a/b.mp3", "jungle", TRACK_INVALID_NAME),
        ("", "jungle", TRACK_INVALID_NAME),
        ("notes.txt", "jungle", TRACK_NOT_AUDIO),
        ("a.mp3", "../etc", TRACK_FOLDER_INVALID),
        ("a.mp3", "nope", TRACK_FOLDER_MISSING),
        ("a.mp3", "jungle/sub/deeper", TRACK_FOLDER_MISSING),
    )
    for name, folder, expected in cases:
        assert asyncio.run(built.save_track(node, folder, name, 11))["status"] == expected, \
            (name, folder)
    assert node.attempts == []
    assert not (library / "a.mp3").exists()


def test_the_service_refuses_a_bidi_override_name_and_writes_nothing(library):
    """The leaf rule is enforced by the SERVICE too, so a caller that skipped the action cannot."""
    node = _Node(staged={13: b"X"})
    built = service(library, node)

    answer = asyncio.run(built.save_track(node, "jungle", "x\u202Exxe.mp3", 13))

    assert answer["status"] == TRACK_INVALID_NAME, answer
    assert node.attempts == []
    assert list((library / "jungle").iterdir()) == []


def test_the_service_refuses_a_node_with_no_music_directory_configured(library):
    node = _Node(staged={12: b"X"})
    built = service(library, node)
    built.locals[DEFAULT_TAG].pop("music_dir")
    assert asyncio.run(built.save_track(node, "", "a.mp3", 12))["status"] == "no-music-dir"
    assert node.attempts == []


# --------------------------------------------------------------------------- the action

class _Service:
    """A ``MusicService`` double: it answers the status it is told and records the call."""

    def __init__(self, status: str = TRACK_OK, **facts):
        self.status = status
        self.facts = facts
        self.calls: list[tuple] = []

    async def save_track(self, node, folder, name, source):
        self.calls.append((getattr(node, "name", ""), folder, name, source))
        if self.status == TRACK_OK:
            return {"status": TRACK_OK, "folder": folder, "name": name,
                    "track": f"{folder}/{name}" if folder else name,
                    "size": int(self.facts.get("size", 0))}
        return dict({"status": self.status, "folder": folder, "name": name}, **self.facts)


def _cluster(*node_names):
    """The bus the context resolves a node through: a mapping of node name to node object."""
    import types

    entries = {name: types.SimpleNamespace(name=name, is_remote=False) for name in node_names}
    return types.SimpleNamespace(nodes=entries, servers={})


def _ctx(node_name="dcs-a-01", *, known=("dcs-a-01",), unrestricted=True):
    """A plain action context: its own node, and the cluster it may see."""
    import types

    from core.actions import ActionContext

    own = types.SimpleNamespace(name=node_name, is_remote=False)
    ctx = ActionContext(node=own, bus=_cluster(*known))
    if not unrestricted:
        ctx._scope = types.SimpleNamespace(allows=lambda names: False, unrestricted=False,
                                          resolved=True)
    return ctx


@pytest.fixture
def upload_actions(monkeypatch):
    """The music actions with the staging seam replaced, and a service double to answer it."""
    import plugins.music.actions as music_actions

    staged: list[tuple] = []
    discarded: list[int] = []

    async def fake_stage(node, name, data):
        staged.append((name, data))
        return len(staged)

    async def fake_discard(node, row_id):
        discarded.append(row_id)

    monkeypatch.setattr(music_actions, "_stage_track_bytes", fake_stage)
    monkeypatch.setattr(music_actions, "_discard_staged_track", fake_discard)
    return music_actions, staged, discarded


@pytest.fixture
def action_service(monkeypatch):
    """Register a ``MusicService`` double for the action's own ``_service()`` lookup."""
    saved = ServiceRegistry._singletons.get(MusicService)

    def install(service):
        ServiceRegistry._singletons[MusicService] = service

    yield install
    if saved is None:
        ServiceRegistry._singletons.pop(MusicService, None)
    else:
        ServiceRegistry._singletons[MusicService] = saved


def test_the_action_refuses_a_name_that_is_not_one_leaf(upload_actions, action_service):
    music_actions, staged, _discarded = upload_actions
    double = _Service()
    action_service(double)

    for refused in ("../x.mp3", "a/b.mp3", "C:\\x.mp3", "C:x.mp3", "\\\\host\\share\\x.mp3"):
        result = asyncio.run(music_actions.upload_music_track(
            _ctx(), folder="jungle", name=refused, source=b"X"))
        assert result.success is False, refused
        assert result.message.startswith(f"'{refused}' is not a file name"), result.message
    empty = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name="", source=b"X"))
    assert empty.message == "A file name is required." 
    assert staged == [] and double.calls == []


def test_the_action_refuses_a_bidi_override_name_by_name(upload_actions, action_service):
    """The ACTION words the refusal, so a console post and a Discord post read the same sentence."""
    music_actions, staged, _discarded = upload_actions
    double = _Service()
    action_service(double)

    name = "x\u202Exxe.mp3"
    result = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name=name, source=b"X"))

    assert result.success is False
    assert result.message.startswith(f"'{name}' is not a file name"), result.message
    assert staged == [] and double.calls == []


def test_the_action_refuses_another_extension_by_name(upload_actions, action_service):
    music_actions, staged, _discarded = upload_actions
    double = _Service()
    action_service(double)

    result = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name="notes.txt", source=b"X"))

    assert result.success is False
    assert "notes.txt" in result.message and ".mp3" in result.message and ".ogg" in result.message
    assert staged == [] and double.calls == []


def test_the_action_refuses_an_oversized_or_empty_upload_with_its_own_sentence(
        upload_actions, action_service, monkeypatch):
    music_actions, staged, _discarded = upload_actions
    double = _Service()
    action_service(double)

    monkeypatch.setattr(music_actions, "MAX_TRACK_UPLOAD_BYTES", 2 * 1024 * 1024)
    oversized = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name=UPLOAD, source=b"X" * (2 * 1024 * 1024 + 1)))
    assert oversized.success is False
    assert oversized.message == music_actions._upload_cap_message()
    assert "2 MiB" in oversized.message

    empty = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name=UPLOAD, source=b""))
    assert empty.success is False
    assert "is empty and was not uploaded" in empty.message
    assert staged == [] and double.calls == []


def test_the_action_refuses_a_path_or_a_url_as_a_source(upload_actions, action_service):
    """A ``web`` caller may only hand BYTES: a path it names is never read by the bot."""
    music_actions, staged, _discarded = upload_actions
    double = _Service()
    action_service(double)

    for source in ("/tmp/x.mp3", "https://example.invalid/x.mp3"):
        result = asyncio.run(music_actions.upload_music_track(
            _ctx(), folder="jungle", name=UPLOAD, source=source))
        assert result.success is False
        assert "as file content" in result.message
    assert staged == [] and double.calls == []


def test_the_action_stages_the_bytes_writes_once_and_discards_its_row(
        upload_actions, action_service):
    music_actions, staged, discarded = upload_actions
    double = _Service(size=len(TRACK_BYTES))
    action_service(double)

    result = asyncio.run(music_actions.upload_music_track(
        _ctx(), folder="jungle", name=UPLOAD, node_name="dcs-a-01",
        source=TRACK_BYTES))

    assert result.success is True, result.message
    assert result.message == f"'{UPLOAD}' uploaded to 'jungle' on node 'dcs-a-01'."
    assert result.data["folder"] == "jungle" and result.data["size"] == len(TRACK_BYTES)
    assert staged == [(UPLOAD, TRACK_BYTES)], "the bytes are staged under the file's own name"
    assert double.calls == [("dcs-a-01", "jungle", UPLOAD, 1)]
    assert discarded == [1], "one write, one staged row, discarded"


def test_the_action_words_each_refusal_the_service_states(upload_actions, action_service):
    music_actions, _staged, discarded = upload_actions
    cases = (
        (TRACK_EXISTS, lambda message: "already holds" in message and "Nothing was overwritten"
                                      in message),
        (TRACK_NOT_AUDIO, lambda message: ".mp3 or a .ogg" in message),
        (TRACK_FOLDER_MISSING, lambda message: "does not exist" in message),
        (TRACK_FOLDER_INVALID, lambda message: "not a folder of the music directory" in message),
        (TRACK_WRITE_ERROR, lambda message: "could not be written" in message),
        ("no-music-dir", lambda message: "No music directory is configured" in message),
    )
    for status, check in cases:
        double = _Service(status)
        action_service(double)
        result = asyncio.run(music_actions.upload_music_track(
            _ctx(), folder="jungle", name=UPLOAD, source=TRACK_BYTES))
        assert result.success is False, status
        assert check(result.message), (status, result.message)
    assert len(discarded) == len(cases) + 0, "every outcome discards its staged row"


def test_the_action_refuses_a_node_outside_the_callers_scope(upload_actions, action_service):
    """A named node goes through the scope: a foreign one is refused before anything is staged."""
    music_actions, staged, _discarded = upload_actions

    double = _Service()
    action_service(double)
    # a restricted caller that cannot see that node at all: "no such node" and "not yours" are the
    # SAME answer here, which is the scope's own rule
    ctx = _ctx(known=(), unrestricted=False)

    result = asyncio.run(music_actions.upload_music_track(
        ctx, folder="jungle", name=UPLOAD, node_name="dcs-b-01", source=TRACK_BYTES))

    assert result.success is False
    assert "outside your scope" in result.message
    assert staged == [] and double.calls == []

# --------------------------------------------------------------------------- the page

class PageMusic(_PageMusic):
    """The music page's own service double, plus the upload write."""

    def __init__(self):
        super().__init__()
        self.saved: list[tuple] = []

    async def save_track(self, node, folder, name, source):
        self.saved.append((getattr(node, "name", ""), folder, name, source))
        return {"status": TRACK_OK, "folder": folder, "name": name,
                "track": f"{folder}/{name}" if folder else name, "size": 0}


@pytest.fixture
def page_service(monkeypatch):
    """Register the page's service double under the real class for this test only."""
    service = PageMusic()
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
def upload_registry():
    """Clear the action registry and re-import the module, as the other music suites do."""
    import sys

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


def app_with(config_dir, *, plugins=("music",)):
    return build(config_dir, fleet(), plugins=plugins)


async def open_editor(http, path=None):
    response = await http.get(path or music_page.playlist_url(PLAYLIST) + "?folder=jungle")
    assert response.status_code == 200, response.text
    return response.text


def _staged_seam(monkeypatch):
    """The action's staging seam, replaced: the console never reaches a database here."""
    import plugins.music.actions as music_actions

    rows: list[bytes] = []

    async def stage(node, name, data):
        rows.append(data)
        return len(rows)

    async def discard(node, row_id):
        return None

    monkeypatch.setattr(music_actions, "_stage_track_bytes", stage)
    monkeypatch.setattr(music_actions, "_discard_staged_track", discard)
    return rows


@pytest.mark.asyncio
async def test_the_library_card_carries_the_upload_control_and_its_drop_target(
        config_dir, upload_registry, page_service):
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        overview = await _overview(http)

    # the ONE door: a multipart form in the library card's header, into the folder on screen
    assert f'action="{music_page.upload_url(PLAYLIST)}"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html and 'accept=".mp3,.ogg"' in html
    assert 'name="folder" value="jungle"' in html
    # the drop target's own state, and the script that enhances it
    assert "data-upload" in html
    assert f'data-upload-url="{music_page.upload_url(PLAYLIST)}"' in html
    assert 'data-upload-folder="jungle"' in html
    assert f'src="/static/{music_page.UPLOAD_SCRIPT}"' in html
    # a rail row states its folder, which is where a drop on it lands
    assert 'data-folder="jungle"' in html
    # ONE DOOR: the overview page carries no upload at all
    assert music_page.upload_url(PLAYLIST) not in overview
    assert "data-upload-url" not in overview
    assert f'src="/static/{music_page.UPLOAD_SCRIPT}"' not in overview


async def _overview(http):
    response = await http.get(music_page.MUSIC_PATH)
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_the_upload_control_is_absent_without_its_capability_and_a_crafted_post_is_refused(
        config_dir, upload_registry, page_service, monkeypatch):
    """An installation that grants ``music.upload`` to Admin only: a DCS Admin sees no control."""
    # the caller the capability is NOT granted to (an installation narrows it on the Access page): the
    # page and the route read the same predicate, so one patch is both the control and the gate
    real_allows = permissions.allows

    def denies_the_upload(capability, roles, *, manager=False):
        if capability == music_page.CAPABILITY_UPLOAD:
            return False
        return real_allows(capability, roles, manager=manager)

    monkeypatch.setattr(permissions, "allows", denies_the_upload)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        assert music_page.upload_url(PLAYLIST) not in html
        assert 'type="file"' not in html
        assert f'src="/static/{music_page.UPLOAD_SCRIPT}"' not in html
        assert "data-upload-url" not in html

        token = token_of(html)
        crafted = await http.post(music_page.upload_url(PLAYLIST),
                                  data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token},
                                  files={"file": (UPLOAD, TRACK_BYTES, "audio/mpeg")})

    assert crafted.status_code == 403
    assert page_service.saved == [], "a refused caller writes nothing"


@pytest.mark.asyncio
async def test_the_upload_post_writes_into_the_selected_folder_and_lands_on_it(
        config_dir, upload_registry, page_service, monkeypatch):
    rows = _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "node": "dcs-a-01",
                                      "_csrf_token": token_of(html)},
                                files={"file": (UPLOAD, TRACK_BYTES, "audio/mpeg")})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == \
            music_page.playlist_url(PLAYLIST) + "?folder=jungle"
        landed = await open_editor(http)

    assert len(page_service.saved) == 1, "one file is one write"
    node_name, folder, name, data = page_service.saved[0]
    assert node_name == "dcs-a-01", "the node the page read the library on"
    assert (folder, name) == ("jungle", UPLOAD)
    assert data == 1, "the service is handed the staged row id, never the bytes"
    assert rows == [TRACK_BYTES], "the bytes reached the action whole"
    assert "uploaded" in landed and UPLOAD in landed, "the notice names the file it stored"


@pytest.mark.asyncio
async def test_the_upload_post_refuses_another_extension_and_writes_nothing(
        config_dir, upload_registry, page_service, monkeypatch):
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token_of(html)},
                                files={"file": ("notes.txt", b"X", "text/plain")})
        assert reply.status_code == 303
        landed = await open_editor(http)

    assert "notes.txt" in landed and ".mp3 or a .ogg" in landed
    assert page_service.saved == [], "the writer never saw it"


@pytest.mark.asyncio
async def test_the_upload_post_refuses_an_oversized_file_without_reading_it(
        config_dir, upload_registry, page_service, monkeypatch):
    monkeypatch.setattr(music_page, "UPLOAD_MAX_BYTES", 2 * 1024 * 1024)
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token_of(html)},
                                files={"file": (UPLOAD, b"X" * (2 * 1024 * 1024 + 1),
                                                 "audio/mpeg")})
        assert reply.status_code == 303
        landed = await open_editor(http)

    assert "2 MiB limit and was not uploaded" in landed
    assert page_service.saved == []


@pytest.mark.asyncio
async def test_a_upload_body_over_the_cap_is_refused_before_it_is_parsed(
        config_dir, upload_registry, page_service, monkeypatch):
    """A DECLARED body that cannot fit the cap is a bare 413, before ``request.form()`` runs.

    Both the cap and the (generous) multipart envelope are shrunk to zero, so the tiny test POST is
    over the line: the proof is the 413 itself and that no writer was reached.
    """
    monkeypatch.setattr(music_page, "UPLOAD_MAX_BYTES", 0)
    monkeypatch.setattr(music_page, "UPLOAD_ENVELOPE_BYTES", 0)
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "_csrf_token": token_of(html)},
                                files={"file": (UPLOAD, b"X", "audio/mpeg")})

    assert reply.status_code == 413, (reply.status_code, reply.text)
    assert page_service.saved == [], "an over-cap body must never reach the writer"


@pytest.mark.asyncio
async def test_the_upload_post_needs_a_file_a_name_and_the_session_token(
        config_dir, upload_registry, page_service, monkeypatch):
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        token = token_of(html)
        no_file = await http.post(music_page.upload_url(PLAYLIST),
                                  data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token})
        assert no_file.status_code == 303
        landed = await open_editor(http)
        assert music_page.UPLOAD_REQUIRED in landed

        no_token = await http.post(music_page.upload_url(PLAYLIST), data={"folder": "jungle"},
                                   files={"file": (UPLOAD, TRACK_BYTES, "audio/mpeg")})
        assert no_token.status_code == 403

    assert page_service.saved == []


@pytest.mark.asyncio
async def test_the_declared_body_over_the_cap_leaves_an_audit_row(config_dir, upload_registry,
                                                                 page_service, recording_bot,
                                                                 monkeypatch):
    """The 413 never reaches the action, so the ROUTE writes the trail — one entry, the actor named."""
    monkeypatch.setattr(music_page, "UPLOAD_MAX_BYTES", 0)
    monkeypatch.setattr(music_page, "UPLOAD_ENVELOPE_BYTES", 0)
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "_csrf_token": token_of(html)},
                                files={"file": (UPLOAD, b"X", "audio/mpeg")})

    assert reply.status_code == 413, (reply.status_code, reply.text)
    assert page_service.saved == []
    assert [call[0] for call in recording_bot.calls] == \
        [f"{music_page._upload_cap_message()}"]


@pytest.mark.asyncio
async def test_a_missing_file_part_leaves_an_audit_row(config_dir, upload_registry, page_service,
                                                      recording_bot, monkeypatch):
    """The console's own "required" refusal is an ATTEMPT too: it is audited like the action's."""
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "node": "dcs-a-01",
                                      "_csrf_token": token_of(html)})
        landed = await open_editor(http)

    assert reply.status_code == 303
    assert music_page.UPLOAD_REQUIRED in landed
    assert page_service.saved == []
    assert [call[0] for call in recording_bot.calls] == \
        [f"{music_page.UPLOAD_REQUIRED}"]


@pytest.mark.asyncio
async def test_a_parsed_part_over_the_cap_leaves_an_audit_row(config_dir, upload_registry,
                                                             page_service, recording_bot,
                                                             monkeypatch):
    """The second cap gate refuses before the bytes are read, and audits like the first."""
    monkeypatch.setattr(music_page, "UPLOAD_MAX_BYTES", 2 * 1024 * 1024)
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        reply = await http.post(music_page.upload_url(PLAYLIST),
                                data={"folder": "jungle", "node": "dcs-a-01",
                                      "_csrf_token": token_of(html)},
                                files={"file": (UPLOAD, b"X" * (2 * 1024 * 1024 + 1),
                                                "audio/mpeg")})

    assert reply.status_code == 303
    assert page_service.saved == []
    assert [call[0] for call in recording_bot.calls] == \
        [f"{music_page._upload_cap_message()}"]


@pytest.mark.asyncio
async def test_the_drop_scripts_post_answers_in_json_and_leaves_no_notice(
        config_dir, upload_registry, page_service, monkeypatch):
    """The card's script words its own sentence: the answer is JSON, and no notice is stored."""
    _staged_seam(monkeypatch)
    app = app_with(config_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)
        token = token_of(html)
        ok = await http.post(music_page.upload_url(PLAYLIST),
                             data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token},
                             files={"file": (UPLOAD, TRACK_BYTES, "audio/mpeg")},
                             headers={"Accept": "application/json"})
        assert ok.status_code == 200
        assert ok.json() == {"ok": True, "message": f"'{UPLOAD}' uploaded to 'jungle' on node 'dcs-a-01'."}

        bad = await http.post(music_page.upload_url(PLAYLIST),
                              data={"folder": "jungle", "node": "dcs-a-01", "_csrf_token": token},
                              files={"file": ("notes.txt", b"X", "text/plain")},
                              headers={"Accept": "application/json"})
        assert bad.status_code == 200
        assert bad.json()["ok"] is False and "notes.txt" in bad.json()["message"]

        # nothing was left behind for the page that follows
        page = await open_editor(http)
        assert "notes.txt" not in page
    assert len(page_service.saved) == 1


@pytest.mark.asyncio
async def test_the_page_read_only_render_draws_no_upload(config_dir, upload_registry, page_service):
    """No music action in this process: no control, and no script that would post to a dead route."""
    app = app_with(config_dir, plugins=())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_editor(http)

    assert music_page.upload_url(PLAYLIST) not in html
    assert f'src="/static/{music_page.UPLOAD_SCRIPT}"' not in html
    assert "data-upload-url" not in html


def test_the_console_cap_and_the_actions_cap_are_one_number_and_one_sentence():
    """The console keeps its own copy (the web surface never imports a plugin module): pin them."""
    import plugins.music.actions as music_actions

    assert music_page.UPLOAD_MAX_BYTES == music_actions.MAX_TRACK_UPLOAD_BYTES
    assert music_page._upload_cap_message() == music_actions._upload_cap_message()


def test_the_pages_capability_name_is_the_action_modules_own():
    import plugins.music.actions as music_actions

    assert music_page.CAPABILITY_UPLOAD == music_actions.CAPABILITY_UPLOAD


def test_the_upload_is_declared_in_the_access_matrix_at_the_same_roles():
    """A declared capability must be placed and explained: the Access page's own ratchet."""
    from services.webservice.pages import access

    grouped = [name for _, members in access.GROUPS for name in members]
    assert music_page.CAPABILITY_UPLOAD in grouped
    assert access.DESCRIPTIONS.get(music_page.CAPABILITY_UPLOAD)
    music_page.declare()
    assert permissions.BASE_CAPABILITIES[music_page.CAPABILITY_UPLOAD] == music_page.UPLOAD_ROLES


@pytest.mark.asyncio
async def test_the_whole_door_puts_a_real_file_in_the_library(library, config_dir, upload_registry,
                                                              monkeypatch):
    """POST through the console → the route → the ACTION → the real SERVICE → the real disk.

    The only doubles are the node's own ``write_file`` (it IS the disk) and the tag reader, so what
    this case proves is the wiring the card is about: the bytes a browser sent land in the folder the
    page showed, and the page's next render lists the track WITHOUT anybody pressing Rescan.
    """
    from test_music_service import Store, SyncPool

    node = _Node(staged={41: TRACK_BYTES})
    built = service(library, node)
    store = Store(playlists={PLAYLIST: ["jungle/a.mp3"]})
    built.apool = store
    built.pool = SyncPool(store)

    async def stage(n, name, data):
        node.staged[41] = data
        return 41

    async def discard(n, row_id):
        return None

    import plugins.music.actions as music_actions

    monkeypatch.setattr(music_actions, "_stage_track_bytes", stage)
    monkeypatch.setattr(music_actions, "_discard_staged_track", discard)
    saved = ServiceRegistry._singletons.get(MusicService)
    ServiceRegistry._singletons[MusicService] = built
    try:
        cluster = fleet()
        # the seam's node view is the LIVE node object, which the proxy wrapper reads `is_remote` off
        cluster.nodes["dcs-a-01"].is_remote = False
        app = build(config_dir, cluster, plugins=("music",))
        async with client(app) as http:
            await sign_in(http, "mod1")
            html = await open_editor(http)
            reply = await http.post(music_page.upload_url(PLAYLIST),
                                    data={"folder": "jungle", "node": "dcs-a-01",
                                          "_csrf_token": token_of(html)},
                                    files={"file": (UPLOAD, TRACK_BYTES, "audio/mpeg")})
            assert reply.status_code == 303, reply.text
            landed = await open_editor(http)
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(MusicService, None)
        else:
            ServiceRegistry._singletons[MusicService] = saved

    assert (library / "jungle" / UPLOAD).read_bytes() == TRACK_BYTES, "the bytes are on the disk"
    assert UPLOAD in landed, "the folder's listing shows the new track after the redirect"
    assert "uploaded to" in landed and "jungle" in landed, "the notice names the folder it went to"


# --------------------------------------------------------------------------- the shipped script

HARNESS = Path(__file__).resolve().parent / "music_upload_harness.js"
UPLOAD_JS = Path(music_page.__file__).resolve().parent.parent / "static" / "music-upload.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def drops() -> dict:
    """The shipped script's own answers, booted against a minimal DOM by the harness."""
    from conftest import run_node_harness

    assert HARNESS.is_file(), "the harness that drives the shipped upload script is missing"
    assert UPLOAD_JS.is_file(), "the shipped upload script is missing"
    done = run_node_harness(["node", str(HARNESS), str(UPLOAD_JS)])
    assert done.returncode == 0, done.stderr
    import json

    return json.loads(done.stdout)


@requires_node
def test_a_drop_on_the_card_posts_the_folder_on_screen(drops):
    events = drops["card"]
    assert len(events["posts"]) == 1, events["posts"]
    body = events["posts"][0]
    assert body["folder"] == ["jungle"], "a drop on the card goes into the folder it shows"
    assert body["file"] == ["Make It Wit Chu.mp3"]
    assert body["_csrf_token"] == ["tok"] and body["node"] == ["dcs-a-01"]
    assert events["landing"] == "/music/Jungle Beats?folder=jungle"


@requires_node
def test_a_drop_on_a_folder_row_posts_that_folder(drops):
    events = drops["row"]
    assert len(events["posts"]) == 1
    assert events["posts"][0]["folder"] == ["jungle/sub"]
    assert events["landing"] == "/music/Jungle Beats?folder=jungle%2Fsub"


@requires_node
def test_several_files_are_posted_one_after_the_other_into_one_folder(drops):
    posts = drops["many"]["posts"]
    assert [body["file"] for body in posts] == [["one.mp3"], ["two.ogg"]]
    assert [body["folder"] for body in posts] == [["jungle"], ["jungle"]]


@requires_node
def test_a_refused_upload_is_said_in_the_card_and_the_page_does_not_leave(drops):
    events = drops["refused"]
    assert events["landing"] == ""
    assert events["note"] == "notes.txt was not uploaded: a track is a .mp3 or a .ogg file."


@requires_node
def test_a_drag_that_carries_no_file_is_ignored(drops):
    events = drops["text"]
    assert events["posts"] == [] and events["prevented"] == 0
