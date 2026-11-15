"""Autoscan on a share: what the handler does with the events a network folder really delivers.

The two shapes a share produces are the ones a local disk does not: an arrival that lands by RENAME
(staged temp name -> final ``.miz``), and a file that keeps growing for far longer than a local
copy, because it is being written over the network.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileMovedEvent
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from core import utils
from core.data.impl.serverimpl import MissionFileSystemEventHandler, ServerImpl, create_observer
from core.utils import helper

REPO = Path(__file__).resolve().parents[1]


class FakeServer:
    """The slice of ``ServerImpl`` the handler touches."""

    name = "test"

    def __init__(self, mission_list=()):
        self.log = logging.getLogger("test.autoscan")
        self.settings = {"missionList": list(mission_list)}
        self.added: list[tuple[str, int]] = []
        self.sizes: list[int] = []
        self.deleted: list[int] = []

    async def addMission(self, path, *, idx: int = -1):
        self.sizes.append(os.path.getsize(path))
        self.added.append((path, idx))

    async def deleteMission(self, idx: int):
        self.deleted.append(idx)


def wait_until(pred, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


@pytest.fixture
def make_handler(loop):
    made: list[MissionFileSystemEventHandler] = []

    def _make(server, **kwargs) -> MissionFileSystemEventHandler:
        handler = MissionFileSystemEventHandler(server, loop, **kwargs)
        made.append(handler)
        return handler

    yield _make
    for handler in made:
        handler.stop()


def start_growing(path: Path) -> threading.Event:
    """Append to *path* from a second thread until the returned event is set."""
    stop = threading.Event()

    def grow():
        with path.open("ab") as f:
            while not stop.is_set():
                f.write(b"x" * 1024)
                f.flush()
                time.sleep(0.1)

    threading.Thread(target=grow, daemon=True).start()
    return stop


def test_a_new_mission_file_is_added(tmp_path, make_handler):
    server = FakeServer()
    handler = make_handler(server)
    mission = tmp_path / "new.miz"
    mission.write_bytes(b"M" * 64)

    handler.on_created(FileCreatedEvent(str(mission)))

    assert wait_until(lambda: server.added)
    assert server.added == [(str(mission), -1)]


def test_a_renamed_upload_is_added_under_its_destination(tmp_path, make_handler):
    """A staging name that no longer exists must not be what reaches the mission list."""
    server = FakeServer()
    handler = make_handler(server)
    staging = tmp_path / "upload.miz.tmp"
    staging.write_bytes(b"x")
    mission = tmp_path / "renamed.miz"
    mission.write_bytes(b"M" * 4096)

    handler.on_moved(FileMovedEvent(str(staging), str(mission)))

    assert wait_until(lambda: server.added)
    assert server.added == [(str(mission), -1)]


def test_a_growing_file_is_added_only_once_it_stops_growing(tmp_path, make_handler):
    server = FakeServer()
    handler = make_handler(server, stable_timeout=30)
    mission = tmp_path / "big.miz"
    mission.write_bytes(b"M")

    stop = start_growing(mission)
    try:
        time.sleep(0.3)
        handler.on_created(FileCreatedEvent(str(mission)))
        time.sleep(1.0)
        assert not server.added, "a half-written mission was added to the list"
    finally:
        stop.set()

    assert wait_until(lambda: server.added)
    assert server.sizes == [mission.stat().st_size]


def test_a_file_that_never_stabilizes_is_skipped_with_a_warning(tmp_path, make_handler, caplog):
    server = FakeServer()
    handler = make_handler(server, stable_timeout=0.5)
    mission = tmp_path / "forever.miz"
    mission.write_bytes(b"M")

    stop = start_growing(mission)
    try:
        with caplog.at_level(logging.WARNING, logger="test.autoscan"):
            handler.on_created(FileCreatedEvent(str(mission)))
            assert wait_until(lambda: "never stabilized" in caplog.text), caplog.text
    finally:
        stop.set()

    assert not server.added


def test_a_duplicate_event_adds_the_file_once(tmp_path, make_handler):
    server = FakeServer()
    handler = make_handler(server)
    mission = tmp_path / "twice.miz"
    mission.write_bytes(b"M" * 16)

    handler.on_created(FileCreatedEvent(str(mission)))
    handler.on_created(FileCreatedEvent(str(mission)))

    assert wait_until(lambda: server.added)
    time.sleep(1.0)
    assert len(server.added) == 1


def test_a_returning_mission_is_re_added_at_its_old_index(tmp_path, make_handler):
    mission = tmp_path / "cycle.miz"
    mission.write_bytes(b"M" * 8)
    server = FakeServer(mission_list=[str(mission)])
    handler = make_handler(server)
    handler.DEBOUNCE_WINDOW = 0

    handler.on_deleted(FileDeletedEvent(str(mission)))
    assert wait_until(lambda: server.deleted == [1])

    handler.on_created(FileCreatedEvent(str(mission)))
    assert wait_until(lambda: server.added == [(str(mission), 1)])


def test_a_local_folder_gets_the_platform_observer_and_a_share_gets_polling():
    assert type(create_observer(False, 3)) is Observer
    assert isinstance(create_observer(True, 3), PollingObserver)


def test_the_configured_mode_wins_and_auto_follows_the_folder(tmp_path, monkeypatch):
    server = object.__new__(ServerImpl)
    server.name = "test"
    server.log = logging.getLogger("test.autoscan")
    server._instance = SimpleNamespace(missions_dir=str(tmp_path))

    server.locals = {"autoscan_observer": "notify"}
    assert ServerImpl._resolve_observer_mode(server) == ("notify", "configured")

    server.locals = {}
    assert ServerImpl._resolve_observer_mode(server)[0] == "notify"      # a local folder

    monkeypatch.setattr(utils, "is_remote_path", lambda _: (True, "\\\\nas\\share\\Missions"))
    assert ServerImpl._resolve_observer_mode(server)[0] == "polling"     # a share

    server.locals = {"autoscan_observer": "nonsense"}
    assert ServerImpl._resolve_observer_mode(server)[0] == "polling"     # unknown falls back to auto


def test_is_remote_path_follows_a_resolved_target(monkeypatch):
    monkeypatch.setattr(helper.os.path, "realpath", lambda _: "\\\\nas\\share\\Missions")
    assert helper.is_remote_path("C:\\DCS\\Missions") == (True, "\\\\nas\\share\\Missions")


def test_is_remote_path_flags_a_mapped_network_drive(monkeypatch):
    monkeypatch.setattr(helper.sys, "platform", "win32")
    monkeypatch.setattr(helper.ctypes, "windll",
                        SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=lambda _: helper.DRIVE_REMOTE)),
                        raising=False)
    monkeypatch.setattr(helper.os.path, "realpath", lambda _: "Z:\\Missions")
    monkeypatch.setattr(helper.os.path, "abspath", lambda path: path)
    monkeypatch.setattr(helper.os.path, "splitdrive", lambda path: ("Z:", path[2:]))
    assert helper.is_remote_path("Z:\\Missions")[0] is True


def test_is_remote_path_reports_a_local_folder_and_never_raises(tmp_path):
    assert helper.is_remote_path(str(tmp_path)) == (False, str(tmp_path))
    is_remote, _ = helper.is_remote_path(str(tmp_path / "gone" / "Missions"))
    assert is_remote is False


def test_both_schemas_declare_the_autoscan_keys():
    for schema_file in ("servers_schema.yaml", "nodes_schema.yaml"):
        text = (REPO / "schemas" / schema_file).read_text(encoding="utf-8")
        document = YAML().load(text)
        keys = set()

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    keys.add(key)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(document)
        assert {"autoscan_observer", "autoscan_poll_interval", "autoscan_stable_timeout"} <= keys, schema_file
        assert "enum: ['auto', 'notify', 'polling']" in text, schema_file


def validate_sample(tmp_path, value: str) -> subprocess.CompletedProcess:
    """Validate a copy of the sample servers.yaml carrying *value* as its observer mode.

    In a subprocess on purpose: the validator registers every port it sees process-wide, and a test
    that did that in-process would decide the port checks of whatever runs after it.
    """
    source = (REPO / "samples" / "servers.yaml").read_text(encoding="utf-8")
    path = tmp_path / f"servers_{value}.yaml"
    path.write_text(source.replace("autoscan_observer: auto", f"autoscan_observer: {value}", 1),
                    encoding="utf-8")
    script = "import sys\nfrom core.utils.validators import validate\n" \
             "validate(sys.argv[1], [sys.argv[2]], raise_exception=True)\n"
    return subprocess.run([sys.executable, "-c", script, str(path),
                           str(REPO / "schemas" / "servers_schema.yaml")],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), timeout=120)


def test_the_schema_enforces_the_observer_modes(tmp_path):
    assert validate_sample(tmp_path, "polling").returncode == 0
    refused = validate_sample(tmp_path, "sometimes")
    assert refused.returncode != 0
    assert "Enum 'sometimes' does not exist" in refused.stdout + refused.stderr
