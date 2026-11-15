"""The crash report's log snapshot and the archive it is delivered in.

Two faults are pinned here. ``snapshot_log`` used to copy ``dcs.log`` blindly: DCS re-creates
that file EMPTY at every start, so a crash inside the first seconds after a (re)start copied
nothing and the operator got a message with no crash content, while the run they wanted was
still in ``dcs.log.old``. And the archive stored the log under its full absolute path, which
opens as a folder chain (``C:`` -> ``Users`` -> ...) and reads as an empty zip.
"""

import logging
import os
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from services.bot import BotService
from services.monitoring.service import MonitoringService

LOG = b"=== Log opened UTC 2026-11-15 11:10:07\n2026-11-15 11:10:30 INFO APP (Main): mods mounted\n"


def _monitor() -> MonitoringService:
    """The shipped method with nothing but the logger it needs."""
    service = object.__new__(MonitoringService)
    service.log = logging.getLogger("monitoring-test")
    return service


def _server(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(instance=SimpleNamespace(home=str(tmp_path)))  # type: ignore[arg-type]


NOW = datetime(2026, 11, 15, 11, 16, 3, tzinfo=timezone.utc)
STAMP = "dcs-20261115-111603.log"


def test_the_current_log_is_copied_when_it_has_content(tmp_path):
    """CONTROL: the ordinary case is unchanged — dcs.log wins when it holds anything."""
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / "dcs.log").write_bytes(LOG)
    (logs / "dcs.log.old").write_bytes(b"previous run\n")

    copy = _monitor().snapshot_log(_server(tmp_path), NOW)  # type: ignore[arg-type]

    assert copy == str(logs / STAMP)
    assert (logs / STAMP).read_bytes() == LOG


def test_an_empty_current_log_falls_back_to_the_previous_run(tmp_path):
    """The reported symptom: a crash right after a start, when dcs.log exists but is empty."""
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / "dcs.log").write_bytes(b"")
    (logs / "dcs.log.old").write_bytes(LOG)

    copy = _monitor().snapshot_log(_server(tmp_path), NOW)  # type: ignore[arg-type]

    assert copy == str(logs / STAMP), "the crash content lives in dcs.log.old and must be copied"
    assert (logs / STAMP).read_bytes() == LOG


def test_no_copy_is_written_when_every_candidate_is_empty(tmp_path):
    """A zero-byte dcs-<ts>.log would be offered by /download as if it were the crash log."""
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / "dcs.log").write_bytes(b"")
    (logs / "dcs.log.old").write_bytes(b"")

    assert _monitor().snapshot_log(_server(tmp_path), NOW) is None  # type: ignore[arg-type]
    assert sorted(p.name for p in logs.iterdir()) == ["dcs.log", "dcs.log.old"]


def test_a_missing_log_directory_is_no_copy_and_no_error(tmp_path):
    assert _monitor().snapshot_log(_server(tmp_path), NOW) is None  # type: ignore[arg-type]


def test_a_held_file_does_not_lose_the_crash_warning(tmp_path, monkeypatch):
    """An unreadable source must degrade to 'no attachment', never to no message at all."""
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / "dcs.log").write_bytes(LOG)

    def locked(*_args, **_kwargs):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr("services.monitoring.service.shutil.copy2", locked)

    assert _monitor().snapshot_log(_server(tmp_path), NOW) is None  # type: ignore[arg-type]
    assert not (logs / STAMP).exists()


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, embed=None, file=None):
        self.sent.append({"content": content, "embed": embed, "file": file})


class _Bot:
    def __init__(self, channel):
        self.locals = {"channels": {"audit": 1}}
        self.channel = channel

    def mention_admin(self, server):
        return ""

    def get_admin_channel(self, server):
        return self.channel

    def get_channel(self, cid):
        return None


class _Server:
    def __init__(self, path: str, data: bytes):
        self.path = path
        self.data = data
        self.node = SimpleNamespace(read_file=self._read_file)

    async def _read_file(self, path: str) -> bytes:
        assert path == self.path, "the alert must read the snapshot it was handed"
        return self.data


def _alert_service(channel: _Channel) -> BotService:
    service = object.__new__(BotService)
    service.bot = _Bot(channel)  # type: ignore[assignment]
    service.log = logging.getLogger("bot-test")
    return service


@pytest.mark.asyncio
async def test_the_archive_holds_the_log_at_its_root_under_its_own_name():
    channel = _Channel()
    # A path with a drive/root and folders, built with the running platform's separator so the
    # test means the same thing on the node and in this suite (zipfile itself rewrites os.sep to
    # '/' inside the archive, which is why the operator saw C:/Users/... in the entry).
    source = os.path.join("C:\\Users\\dcsuser\\Saved Games\\training01\\Logs", STAMP)
    server = _Server(source, LOG)

    await _alert_service(channel).alert("Server \"training01\" died!", "Setting state to SHUTDOWN.",
                                        server=server, filename=server.path)  # type: ignore[arg-type]

    attachment = channel.sent[0]["file"]
    assert attachment is not None, "the crash log must be attached"
    assert attachment.filename == STAMP + ".zip"
    with zipfile.ZipFile(BytesIO(attachment.fp.read())) as zf:
        assert zf.namelist() == [STAMP], "the entry is the file name, never the path that led to it"
        assert zf.read(STAMP) == LOG


@pytest.mark.asyncio
async def test_an_empty_read_is_not_sent_as_an_empty_archive():
    """CONTROL: the guard that already shipped — nothing is attached when there is nothing to send."""
    channel = _Channel()
    server = _Server("C:\\Logs\\" + STAMP, b"")

    await _alert_service(channel).alert("Server \"training01\" died!", "Setting state to SHUTDOWN.",
                                        server=server, filename=server.path)  # type: ignore[arg-type]

    assert channel.sent[0]["file"] is None
