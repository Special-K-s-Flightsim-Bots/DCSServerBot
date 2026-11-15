"""Pins for the MUSIC action layer.

Every playlist read, playlist write and radio write the console, the REST surface and MCP reach goes
through one ``@action`` function in ``plugins.music.actions``. These cases drive those functions with
a RECORDING service double: the questions are the ones the action layer owns — which node a playlist
resolves on, whether a track path that leaves the music directory is refused before anything is
called, whether a reorder that is not a permutation is refused, that a rename and a delete answer
with the tables they moved, that a radio write is resolved through the caller's scope, that each
setting is validated against its own table of values, and that EVERY write writes its audit entry.
"""
from __future__ import annotations

import re
import types

import pytest

from core.action_results import ActionResult
from core.actions import (AUDIT_RECORDED, TRANSPORT_DISCORD, TRANSPORT_PLUGIN, TRANSPORT_SERVICE,
                          TRANSPORT_WEB, ActionContext, action_registry, call_action,
                          discover_actions, read_action)
from core.services.registry import ServiceRegistry

from plugins.music import actions as music

SERVER = "SRS-1"
NODE = "NODE-01"


# --------------------------------------------------------------------------- doubles

class RecordingService:
    """A ``MusicService`` double: records every call and answers what a case states."""

    def __init__(self, **answers):
        self.calls: list[tuple] = []
        self.answers = dict(answers)

    def _record(self, method, *args):
        def value(arg):
            name = getattr(arg, "name", None)
            return str(name) if name is not None else arg

        self.calls.append((method,) + tuple(value(arg) for arg in args))

    async def _answer(self, method):
        value = self.answers.get(method, None)
        if isinstance(value, BaseException):
            raise value
        return value

    @property
    def methods(self) -> list[str]:
        return [call[0] for call in self.calls]

    async def playlist_usage(self):
        self._record("playlist_usage")
        return list(await self._answer("playlist_usage") or [])

    async def list_playlists(self, node, refresh=False):
        self._record("list_playlists", node, refresh)
        return list(await self._answer("list_playlists") or [])

    async def get_playlist(self, node, name, refresh=False):
        self._record("get_playlist", node, name, refresh)
        return await self._answer("get_playlist")

    async def read_library(self, node, refresh=False):
        self._record("read_library", node, refresh)
        return await self._answer("read_library") or {}

    async def read_radios(self, server, refresh=False):
        self._record("read_radios", server, refresh)
        return list(await self._answer("read_radios") or [])

    async def playlist_exists(self, name):
        self._record("playlist_exists", name)
        return bool(await self._answer("playlist_exists"))

    async def add_tracks(self, playlist, files):
        self._record("add_tracks", playlist, ",".join(files))
        return await self._answer("add_tracks") or {}

    async def remove_tracks(self, playlist, song_ids):
        self._record("remove_tracks", playlist, ",".join(str(item) for item in song_ids))
        return await self._answer("remove_tracks") or {}

    async def reorder_tracks(self, playlist, order):
        self._record("reorder_tracks", playlist, ",".join(str(item) for item in order))
        return await self._answer("reorder_tracks") or {}

    async def rename_playlist(self, playlist, name):
        self._record("rename_playlist", playlist, name)
        return await self._answer("rename_playlist") or {}

    async def delete_playlist(self, playlist):
        self._record("delete_playlist", playlist)
        return await self._answer("delete_playlist") or {}

    async def assign_playlist(self, server, radio_name, playlist):
        self._record("assign_playlist", server, radio_name, playlist)
        return await self._answer("assign_playlist") or ""

    async def start_radio(self, server, radio_name):
        self._record("start_radio", server, radio_name)
        return await self._answer("start_radio") or ""

    async def stop_radio(self, server, radio_name):
        self._record("stop_radio", server, radio_name)
        return await self._answer("stop_radio") or ""

    async def skip_radio(self, server, radio_name):
        self._record("skip_radio", server, radio_name)
        return await self._answer("skip_radio") or ""

    async def set_mode(self, server, radio_name, mode):
        self._record("set_mode", server, radio_name, getattr(mode, "name", mode))
        return await self._answer("set_mode") or ""

    async def set_radio_config(self, server, radio_name, config):
        self._record("set_radio_config", server, radio_name, config)
        return await self._answer("set_radio_config") or ""


class AuditBot:
    def __init__(self):
        self.entries: list[str] = []

    async def audit(self, message, **kwargs):
        self.entries.append(str(message))


@pytest.fixture(autouse=True)
def discovered():
    """Discover the plugin's actions — the shared fixtures empty the process registry per test."""
    discover_actions(["music"])
    yield


@pytest.fixture(autouse=True)
def no_service(monkeypatch):
    from services.music import MusicService

    monkeypatch.delitem(ServiceRegistry._singletons, MusicService, raising=False)


@pytest.fixture
def registered(monkeypatch):
    from services.music import MusicService

    def install(double):
        monkeypatch.setitem(ServiceRegistry._singletons, MusicService, double)
        return double

    return install


@pytest.fixture
def audited(monkeypatch):
    from services.bot.service import BotService

    bot = AuditBot()
    monkeypatch.setitem(ServiceRegistry._singletons, BotService,
                        types.SimpleNamespace(bot=bot))
    return bot


def node(name=NODE):
    return types.SimpleNamespace(name=name, display_name=name, is_remote=False)


def server(name=SERVER, node_name=NODE, managed_by=("Blue Flag",)):
    return types.SimpleNamespace(
        name=name, display_name=name, node=node(node_name),
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )


def everywhere():
    return types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=True, resolved=True)


def nothing_outside(managed_by=("Blue Flag",)):
    return types.SimpleNamespace(
        allows=lambda names: any(name in managed_by for name in (names or ())),
        unrestricted=False, resolved=True)


def context(*, scope=None, nodes=(NODE,), servers=None, own=NODE, transport=TRANSPORT_WEB):
    cluster = types.SimpleNamespace(
        servers={item.name: item for item in (servers if servers is not None else [server()])},
        nodes={name: node(name) for name in nodes},
    )
    ctx = ActionContext(node=node(own), bus=cluster)
    ctx._scope = everywhere() if scope is None else scope
    # the console is the only transport a playlist WRITE admits, so a write case states it; a case
    # that asserts the refusal states the transport it refuses
    ctx._transport = transport
    return ctx


# ------------------------------------------------------------------- the registry declaration

def test_the_module_registers_the_thirteen_actions():
    assert {name for name in action_registry()
            if name in {"list_music_playlists", "get_music_playlist", "read_music_library",
                        "get_music_radios", "add_playlist_track", "remove_playlist_track",
                        "reorder_playlist", "rename_playlist", "delete_playlist",
                        "upload_music_track",
                        "set_radio_playlist", "control_radio", "set_radio_config"}} == {
        "list_music_playlists", "get_music_playlist", "read_music_library", "get_music_radios",
        "add_playlist_track", "remove_playlist_track", "reorder_playlist", "rename_playlist",
        "delete_playlist", "upload_music_track",
        "set_radio_playlist", "control_radio", "set_radio_config",
    }


def test_the_module_imports_core_and_nothing_else_at_import_time():
    """Discovery imports this file on every install: it must not pull the service's stack."""
    import ast
    import pathlib

    source = pathlib.Path(music.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imports.extend(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            imports.append(statement.module or "")
    assert imports
    # ``core`` and the standard library only: the service's own stack is imported per call
    offenders = [name for name in imports
                 if name.startswith(("services", "plugins", "extensions", "discord", "psycopg",
                                     "eyed3", "fastapi"))]
    assert offenders == [], f"these imports run at import time: {offenders}"


# ------------------------------------------------------------------- the reads

def test_the_playlist_list_names_the_radios_that_use_each_playlist(registered):
    service = registered(RecordingService(
        playlist_usage=[{"playlist": "Rock", "server": SERVER, "radio": "Radio 1"},
                        {"playlist": "Pop", "server": "SRS-2", "radio": "Radio 2"}],
        list_playlists=[{"name": "Rock", "tracks": 3, "seconds": 120.0, "missing": 1}],
    ))
    result = __import__("asyncio").run(
        read_action("list_music_playlists", context(servers=[server(), server("SRS-2")])))
    assert set(result.data) >= {"node_name", "playlists"}
    assert result.success is True
    assert result.node_name == NODE
    assert result.playlists[0]["used_by"] == [{"server": SERVER, "radio": "Radio 1"}]
    assert service.calls[1][0] == "list_playlists"
    assert service.calls[1][1] == NODE


def test_a_named_node_is_resolved_through_the_callers_view(registered):
    registered(RecordingService(playlist_usage=[], list_playlists=[]))
    result = __import__("asyncio").run(
        read_action("list_music_playlists", context(scope=nothing_outside(), nodes=()),
                    node_name="NODE-99"))
    assert result.success is False
    assert "outside your scope" in result.message or "offline or unknown" in result.message


def test_a_scoped_caller_does_not_receive_the_node_of_a_server_it_cannot_see(registered):
    """A playlist's node comes from its radios' servers — resolved through the caller's scope."""
    service = registered(RecordingService(
        playlist_usage=[{"playlist": "Rock", "server": SERVER, "radio": "Radio 1"}],
        list_playlists=[{"name": "Rock", "tracks": 1, "seconds": 10.0, "missing": 0}],
    ))
    result = __import__("asyncio").run(read_action(
        "list_music_playlists",
        context(scope=nothing_outside(managed_by=("Hoster",)), nodes=(NODE, "NODE-02"),
                servers=[server(SERVER, node_name="NODE-02")], own=NODE),
        ))
    assert result.success is True
    assert result.node_name == NODE
    assert result.node_name != "NODE-02"
    assert service.calls[1][0] == "list_playlists"
    assert service.calls[1][1] == NODE


def test_the_playlist_node_follows_an_in_scope_assignment(registered):
    service = registered(RecordingService(
        playlist_usage=[{"playlist": "Rock", "server": SERVER, "radio": "Radio 1"}],
        get_playlist={"name": "Rock", "tracks": [], "seconds": 0.0, "missing": 0},
    ))
    result = __import__("asyncio").run(read_action(
        "get_music_playlist",
        context(nodes=(NODE, "NODE-02"), servers=[server(SERVER, node_name="NODE-02")], own=NODE),
        playlist="Rock"))
    assert result.success is True
    assert result.node_name == "NODE-02"


def test_a_missing_playlist_is_a_typed_refusal(registered):
    registered(RecordingService(playlist_usage=[], get_playlist=None))
    result = __import__("asyncio").run(
        read_action("get_music_playlist", context(), playlist="Nowhere"))
    assert result.success is False
    assert result.message == "Playlist 'Nowhere' not found."


def test_the_playlist_read_carries_the_tracks_and_the_missing_count(registered):
    registered(RecordingService(playlist_usage=[], get_playlist={
        "name": "Rock", "tracks": [{"song_id": 1, "file": "a.mp3", "present": True},
                                   {"song_id": 2, "file": "b.mp3", "present": False}],
        "seconds": 90.0, "missing": 1}))
    result = __import__("asyncio").run(
        read_action("get_music_playlist", context(), playlist="rock"))
    assert result.success is True
    assert result.playlist == "Rock"
    assert result.missing == 1
    assert result.seconds == 90.0
    assert len(result.tracks) == 2


def test_the_library_read_answers_the_callers_own_node(registered):
    service = registered(RecordingService(read_library={
        "node": NODE, "folders": [{"path": "", "count": 2}], "files": 2,
        "seconds": 40.0, "scanned": 1234.0}))
    result = __import__("asyncio").run(read_action("read_music_library", context()))
    assert result.success is True
    assert result.node_name == NODE
    assert result.files == 2
    assert service.calls[0][0] == "read_library"


#: a disk path as the payload would carry one: POSIX absolute or a Windows drive letter
_ABSOLUTE_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


def _absolute_strings(value) -> list[str]:
    """Every string in *value* that looks like an absolute filesystem path, keys included."""
    found: list[str] = []
    if isinstance(value, str):
        if _ABSOLUTE_PATH.match(value):
            found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_absolute_strings(key))
            found.extend(_absolute_strings(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_absolute_strings(item))
    return found


def test_the_library_read_keeps_the_nodes_disk_path_off_the_wire(registered):
    """The payload reaches every caller holding the read capability, an API key included, so the
    node's absolute music directory is not part of it — even when the service hands one back.

    The folder tree itself is unchanged: its entries stay relative to the music directory.
    """
    registered(RecordingService(read_library={
        "node": NODE, "root": "/srv/dcs/music", "folders": [{"path": "", "count": 1},
                                                            {"path": "rock", "count": 1}],
        "files": 2, "seconds": 10.0, "scanned": 1234.0}))
    result = __import__("asyncio").run(read_action("read_music_library", context()))
    assert result.success is True
    assert "root" not in result.data
    assert _absolute_strings(result.data) == []
    assert [folder["path"] for folder in result.data["folders"]] == ["", "rock"]


def test_the_radios_of_a_foreign_server_are_refused(registered):
    registered(RecordingService(read_radios=[]))
    result = __import__("asyncio").run(
        read_action("get_music_radios", context(scope=nothing_outside()), server_name="OTHER"))
    assert result.success is False
    assert "outside your scope" in result.message


def test_an_installation_without_the_plugin_answers_a_typed_sentence():
    result = __import__("asyncio").run(
        read_action("list_music_playlists", context()))
    assert result.success is False
    assert result.message == music.SERVICE_MISSING


# ------------------------------------------------------------------- the playlist writes

def _add(ctx, service, *, playlist="Rock", song_files=("a.mp3",)):
    return __import__("asyncio").run(
        call_action("add_playlist_track", ctx, playlist=playlist, song_files=list(song_files)))


def test_a_track_path_that_leaves_the_music_directory_is_refused_and_nothing_is_written(
        registered, audited):
    service = registered(RecordingService())
    for escape in ("../../etc/passwd.mp3", "/etc/passwd.mp3", "C:/music/a.mp3", "a/../../b.mp3"):
        result = _add(context(), service, song_files=(escape,))
        assert result.success is False
        assert "not a track of the music directory" in result.message
    assert "add_tracks" not in service.methods
    assert audited.entries          # a refusal is an entry too


def test_the_first_track_creates_the_playlist(registered, audited):
    service = registered(RecordingService(add_tracks={
        "status": "ok", "playlist": "Rock", "added": 2, "tracks": 2, "created": True}))
    result = _add(context(), service, song_files=("rock/a.mp3", "rock/b.mp3"))
    assert result.success is True
    assert result.message == "Playlist 'Rock' created with 2 track(s)."
    assert result.tracks == 2
    assert service.calls == [("add_tracks", "Rock", "rock/a.mp3,rock/b.mp3")]
    assert result.data["audit"] == AUDIT_RECORDED


def test_the_paths_are_normalised_and_repeats_of_one_call_collapsed(registered, audited):
    service = registered(RecordingService(add_tracks={
        "status": "ok", "playlist": "Rock", "added": 2, "tracks": 2, "created": False}))
    result = _add(context(), service, song_files=("rock/a.mp3", "./rock/a.mp3", "rock\\b.mp3"))
    assert result.success is True
    assert service.calls == [("add_tracks", "Rock", "rock/a.mp3,rock/b.mp3")]


def test_removing_a_track_names_its_id_and_an_emptied_playlist_says_so(registered, audited):
    service = registered(RecordingService(remove_tracks={
        "status": "ok", "playlist": "Rock", "removed": 1, "tracks": 0, "unmapped": 1}))
    result = __import__("asyncio").run(
        call_action("remove_playlist_track", context(), playlist="Rock", song_ids=["7"]))
    assert result.success is True
    assert "removed from playlist 'Rock'" in result.message
    assert "1 radio assignment(s) were removed" in result.message
    assert service.calls == [("remove_tracks", "Rock", "7")]


def test_removing_without_a_positive_id_is_refused_before_the_service(registered, audited):
    service = registered(RecordingService())
    for ids in ([], ["0"], ["-3"], ["abc"], [""]):
        result = __import__("asyncio").run(
            call_action("remove_playlist_track", context(), playlist="Rock", song_ids=ids))
        assert result.success is False
        assert "positive id" in result.message
    assert "remove_tracks" not in service.methods


def test_a_reorder_that_is_not_a_permutation_is_refused_with_its_own_sentence(registered, audited):
    service = registered(RecordingService(reorder_tracks={
        "status": "not-a-permutation", "playlist": "Rock", "tracks": 3}))
    result = __import__("asyncio").run(
        call_action("reorder_playlist", context(), playlist="Rock", order=[3, 1, 9]))
    assert result.success is False
    assert "Reload the page" in result.message
    assert service.calls == [("reorder_tracks", "Rock", "3,1,9")]
    assert result.data["audit"] == AUDIT_RECORDED


def test_a_permutation_is_saved(registered, audited):
    registered(RecordingService(reorder_tracks={"status": "ok", "playlist": "Rock", "tracks": 3}))
    result = __import__("asyncio").run(
        call_action("reorder_playlist", context(), playlist="Rock", order=[3, 1, 2]))
    assert result.success is True
    assert "order of playlist 'Rock' was saved" in result.message


def test_the_order_is_deduplicated_before_the_service(registered, audited):
    service = registered(RecordingService(reorder_tracks={
        "status": "not-a-permutation", "playlist": "Rock", "tracks": 2}))
    __import__("asyncio").run(
        call_action("reorder_playlist", context(), playlist="Rock", order=[1, 1]))
    assert service.calls == [("reorder_tracks", "Rock", "1")]


def test_a_rename_that_collides_is_refused(registered, audited):
    registered(RecordingService(rename_playlist={
        "status": "exists", "playlist": "Rock", "previous": "Rock", "tracks": 0, "radios": 0}))
    result = __import__("asyncio").run(
        call_action("rename_playlist", context(), playlist="Rock", name="Pop"))
    assert result.success is False
    assert result.message == "Not done: a playlist named 'Pop' already exists."


def test_a_rename_names_the_assignments_it_moved(registered, audited):
    registered(RecordingService(rename_playlist={
        "status": "ok", "playlist": "Metal", "previous": "Rock", "tracks": 4, "radios": 2}))
    result = __import__("asyncio").run(
        call_action("rename_playlist", context(), playlist="Rock", name="Metal"))
    assert result.success is True
    assert result.playlist == "Metal"
    assert "2 radio assignment(s) moved" in result.message


def test_a_rename_to_its_own_name_is_refused_without_a_write(registered, audited):
    service = registered(RecordingService())
    result = __import__("asyncio").run(
        call_action("rename_playlist", context(), playlist="Rock", name=" Rock "))
    assert result.success is False
    assert "already has that name" in result.message
    assert "rename_playlist" not in service.methods


def test_a_delete_names_the_tracks_and_the_assignments(registered, audited):
    registered(RecordingService(delete_playlist={
        "status": "ok", "playlist": "Rock", "tracks": 5, "unmapped": 1}))
    result = __import__("asyncio").run(call_action("delete_playlist", context(), playlist="Rock"))
    assert result.success is True
    assert result.message == ("Playlist 'Rock' deleted (5 track(s)). "
                              "1 radio assignment(s) were removed with it.")


def test_a_write_without_the_plugin_is_refused_and_audited(audited):
    result = __import__("asyncio").run(call_action("delete_playlist", context(), playlist="Rock"))
    assert result.success is False
    assert result.message == music.SERVICE_MISSING
    assert audited.entries


# ------------------------------------------------------------------- the radio writes

def test_a_radio_write_on_a_foreign_server_is_refused_before_the_service(registered, audited):
    service = registered(RecordingService())
    result = __import__("asyncio").run(
        call_action("set_radio_playlist", context(scope=nothing_outside()), server_name="OTHER",
                    radio_name="Radio 1", playlist="Rock"))
    assert result.success is False
    assert "outside your scope" in result.message
    assert service.calls == []


def test_assigning_a_playlist_that_does_not_exist_is_refused(registered, audited):
    service = registered(RecordingService(playlist_exists=False))
    result = __import__("asyncio").run(
        call_action("set_radio_playlist", context(), server_name=SERVER, radio_name="Radio 1",
                    playlist="Nowhere"))
    assert result.success is False
    assert result.message == "Playlist 'Nowhere' not found."
    assert "assign_playlist" not in service.methods


def test_assigning_a_playlist_reports_the_radios_after_the_write(registered, audited):
    service = registered(RecordingService(
        playlist_exists=True, assign_playlist="",
        read_radios=[{"name": "Radio 1", "on_air": True, "playlist": "Rock"}]))
    result = __import__("asyncio").run(
        call_action("set_radio_playlist", context(), server_name=SERVER, radio_name="Radio 1",
                    playlist="Rock"))
    assert result.success is True
    assert result.message == "Radio 'Radio 1' on 'SRS-1' now plays playlist 'Rock'."
    assert result.radios[0]["playlist"] == "Rock"
    assert service.calls[0] == ("playlist_exists", "Rock")
    assert service.calls[1] == ("assign_playlist", SERVER, "Radio 1", "Rock")


def test_a_radio_the_config_does_not_declare_is_refused_with_its_own_sentence(registered, audited):
    registered(RecordingService(playlist_exists=True, assign_playlist="unknown-radio"))
    result = __import__("asyncio").run(
        call_action("set_radio_playlist", context(), server_name=SERVER, radio_name="Radio 9",
                    playlist="Rock"))
    assert result.success is False
    assert "not configured for server 'SRS-1'" in result.message


def test_an_unknown_control_word_is_refused_before_the_service(registered, audited):
    service = registered(RecordingService())
    result = __import__("asyncio").run(
        call_action("control_radio", context(), server_name=SERVER, radio_name="Radio 1",
                    control="rewind"))
    assert result.success is False
    assert "'rewind' is not a radio control" in result.message
    assert service.calls == []


@pytest.mark.parametrize("control,method", [("play", "start_radio"), ("stop", "stop_radio"),
                                            ("skip", "skip_radio")])
def test_each_control_reaches_its_own_service_call(registered, audited, control, method):
    service = registered(RecordingService(**{method: "", "read_radios": []}))
    result = __import__("asyncio").run(
        call_action("control_radio", context(), server_name=SERVER, radio_name="Radio 1",
                    control=control))
    assert result.success is True
    assert service.methods[0] == method
    assert result.data["audit"] == AUDIT_RECORDED


def test_the_mode_control_carries_the_mode_and_refuses_anything_else(registered, audited):
    service = registered(RecordingService(set_mode="", read_radios=[]))
    result = __import__("asyncio").run(
        call_action("control_radio", context(), server_name=SERVER, radio_name="Radio 1",
                    control="mode", mode="shuffle"))
    assert result.success is True
    assert service.calls[0] == ("set_mode", SERVER, "Radio 1", "SHUFFLE")
    refused = __import__("asyncio").run(
        call_action("control_radio", context(), server_name=SERVER, radio_name="Radio 1",
                    control="mode", mode="loud"))
    assert refused.success is False
    assert "one of repeat, shuffle" in refused.message


def test_an_idle_radio_says_why_it_cannot_be_driven(registered, audited):
    registered(RecordingService(start_radio="radio-idle"))
    result = __import__("asyncio").run(
        call_action("control_radio", context(), server_name=SERVER, radio_name="Radio 1",
                    control="play"))
    assert result.success is False
    assert "no live player" in result.message


def test_every_stated_setting_is_validated_against_its_own_table(registered, audited):
    service = registered(RecordingService(set_radio_config="", read_radios=[]))
    bad = ({"frequency": "-1"}, {"frequency": "abc"}, {"modulation": "USB"},
           {"coalition": "3"}, {"volume": "2"}, {"display_name": "ab"},
           {"display_name": "x" * 31})
    for params in bad:
        result = __import__("asyncio").run(
            call_action("set_radio_config", context(), server_name=SERVER, radio_name="Radio 1",
                        **params))
        assert result.success is False, params
    assert "set_radio_config" not in service.methods


def test_only_the_stated_settings_are_written(registered, audited):
    service = registered(RecordingService(set_radio_config="", read_radios=[]))
    result = __import__("asyncio").run(
        call_action("set_radio_config", context(), server_name=SERVER, radio_name="Radio 1",
                    frequency="31.5", modulation="am", coalition="2", volume="0.25",
                    display_name="  Night   Jazz "))
    assert result.success is True
    assert service.calls[0][0] == "set_radio_config"
    assert service.calls[0][3] == {"frequency": "31.5", "modulation": "AM", "coalition": 2,
                                   "volume": "0.25", "display_name": "Night Jazz"}
    assert result.data["audit"] == AUDIT_RECORDED


def test_a_setting_write_with_no_stated_value_is_refused(registered, audited):
    service = registered(RecordingService())
    result = __import__("asyncio").run(
        call_action("set_radio_config", context(), server_name=SERVER, radio_name="Radio 1"))
    assert result.success is False
    assert result.message == "No radio setting was stated."
    assert service.calls == []


def test_the_accepted_values_are_the_ones_the_radio_takes():
    assert music._frequency("30.0") == "30.0"
    assert music._frequency("9999.5") == "9999.5"
    assert music._frequency("10000") is None
    assert music._frequency("0") is None
    assert music._display_name("abc") == "abc"
    assert music._display_name("x" * 30) == "x" * 30
    assert music._display_name("x" * 31) is None
    assert music._coalition("1") == 1
    assert music._coalition(2) == 2
    assert music._coalition("0") is None


# --------------------------------------------------- the playlist writes: the console is the only door

#: Every PLAYLIST write and the arguments one call needs — the five the guard must cover.
PLAYLIST_WRITES = {
    "add_playlist_track": {"playlist": "Rock", "song_files": ["rock/a.mp3"]},
    "remove_playlist_track": {"playlist": "Rock", "song_ids": [1]},
    "reorder_playlist": {"playlist": "Rock", "order": [1]},
    "rename_playlist": {"playlist": "Rock", "name": "Pop"},
    "delete_playlist": {"playlist": "Rock"},
}


@pytest.mark.parametrize("transport", [TRANSPORT_PLUGIN, TRANSPORT_SERVICE, TRANSPORT_DISCORD, "",
                                       "smtp"])
def test_every_playlist_write_is_refused_outside_the_console(registered, audited, transport):
    """A playlist names no server, so no transport but the console may edit one — a labelled or an
    unnamed one alike, and nothing reaches the service.

    The sentence names the ONE open door: a refusal that lists only some of the shut ones reads
    narrower than the gate and invites a reader to re-open the code to check it.
    """
    service = registered(RecordingService())
    for name, arguments in PLAYLIST_WRITES.items():
        result = __import__("asyncio").run(
            call_action(name, context(transport=transport), **arguments))
        assert result.success is False, name
        assert result.message == music.PLAYLIST_TRANSPORT_REFUSAL, name
        assert "from the website only" in result.message, name
        assert result.message.count(". ") == 0, name      # one sentence
        assert result.data["audit"] == AUDIT_RECORDED, name
    assert service.calls == []


def test_the_console_reaches_every_playlist_write(registered, audited):
    registered(RecordingService(add_tracks={"status": "ok", "playlist": "Rock", "added": 1,
                                           "tracks": 1},
                                remove_tracks={"status": "ok", "removed": 1, "tracks": 0},
                                reorder_tracks={"status": "ok", "tracks": 1},
                                rename_playlist={"status": "ok", "tracks": 1, "radios": 0},
                                delete_playlist={"status": "ok", "tracks": 1, "unmapped": 0}))
    for name, arguments in PLAYLIST_WRITES.items():
        result = __import__("asyncio").run(
            call_action(name, context(transport=TRANSPORT_WEB), **arguments))
        assert result.success is True, (name, result.message)


def test_a_playlist_read_is_not_guarded_by_the_transport(registered):
    """The reads name no server either, but what they answer is playlist names and track paths."""
    registered(RecordingService(playlist_usage=[], list_playlists=[]))
    result = __import__("asyncio").run(
        read_action("list_music_playlists", context(transport=TRANSPORT_PLUGIN)))
    assert result.success is True


def test_adding_a_track_the_playlist_holds_already_is_its_own_sentence(registered, audited):
    service = registered(RecordingService(add_tracks={
        "status": "duplicate", "playlist": "Rock", "added": 0, "already": 1, "tracks": 1}))
    result = __import__("asyncio").run(
        call_action("add_playlist_track", context(), playlist="Rock", song_files=["rock/a.mp3"]))
    assert result.success is False
    assert "already in playlist 'Rock'" in result.message
    assert result.changed == 0
    assert service.methods == ["add_tracks"]


def test_adding_a_folder_names_both_counts(registered, audited):
    registered(RecordingService(add_tracks={
        "status": "ok", "playlist": "Rock", "added": 1, "already": 2, "tracks": 3}))
    result = __import__("asyncio").run(
        call_action("add_playlist_track", context(), playlist="Rock",
                    song_files=["rock/a.mp3", "rock/b.mp3", "rock/c.mp3"]))
    assert result.success is True
    assert "1 track(s) added to playlist 'Rock'." in result.message
    assert "2 track(s) were already in playlist 'Rock'." in result.message
    assert result.data["already"] == 2
