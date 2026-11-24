"""The mission-file seam: ``upload_mission`` / ``download_mission`` / ``get_mission_list``.

Three actions are the ONE implementation of the mission-file operations — the write goes through
``Server.uploadMission`` (never a direct file write), the newest-copy resolution is the bot's own
``Server.getAllMissionFiles``, and ``missionList`` is never hand-written. The REST endpoint
(``plugins/restapi/commands.py::mission_upload``) re-uses ``upload_mission`` instead of its own
unsanitised join + ``aiofiles`` write, so a traversal name is refused by the SAME gate whichever
transport posted it.

Contract pinned here:

* a posted NAME must be a leaf, carry no separator of either kind, not be ``.``/``..`` and end in
  ``.miz`` — ``../evil.miz``, an absolute path, a directory component and a non-``.miz`` name are
  each refused with NOTHING written and NOTHING staged;
* the happy path stages the bytes in the ``files`` table (the route ``NodeProxy`` uses) and passes
  the row id to ``uploadMission``, whose typed outcome (``FILE_EXISTS`` / ``FILE_IN_USE``) rides the
  result untouched;
* the STAGED row is cleaned up unconditionally and idempotently: a typed refusal, a ``WRITE_ERROR``
  or a raised exception leaves NO row (and no mission bytes) behind, and a success deletes it once;
* the ``str`` source's trust level is explicit: BYTES from any transport; an http(s) URL fetched and
  size-capped AS the bytes arrive (refuse whole) and staged; a bare PATH accepted only from a caller
  that did not come through a request (Discord / the in-process service), never from ``web`` or
  ``plugin`` (REST), and never from an unnamed context;
* a bytes source over the cap is refused WHOLE;
* the download resolves the NEWEST copy and serves the LOGICAL filename with the bytes on the result;
* the list is 1-based, names the physical copy in use and reports the rotation state.
"""
from __future__ import annotations

import logging
import os
import sys
import types

import pytest

from fastapi import HTTPException

import core.actions as actions_module
from core import Status, UploadStatus
from core.actions import (
    TRANSPORT_DISCORD,
    TRANSPORT_PLUGIN,
    TRANSPORT_SERVICE,
    TRANSPORT_WEB,
    ActionContext,
    action_registry,
    discover_actions,
)
from core.services.registry import ServiceRegistry
from core.data.impl.serverimpl import MISSION_LIST_BUSY_REFUSAL as BOT_REFUSAL
from plugins.restapi.commands import RestAPI
from services.servicebus import ServiceBus


# ------------------------------------------------------------------------------- test doubles

#: ONE host spelling of the fake mission tree. ``"/missions"`` is absolute on POSIX but NOT on
#: Windows, where the bot's own ``abspath`` would prepend the CWD's drive — and every raw literal in
#: its raw POSIX spelling then misses the host-joined value the double is asked for. The doubles and
#: the expectations therefore share these two helpers.
MISSIONS = os.path.abspath("/missions")


def m(*parts: str) -> str:
    """*parts* joined below :data:`MISSIONS`, normalised the way the bot normalises a path."""
    return os.path.normpath(os.path.join(MISSIONS, *parts))


class _FilesTable:
    """The ``files`` table as a dict — ``id -> inserted params`` — with the DELETEs recorded.

    Modelling the table (and not just an ever-growing list) is what makes the orphan-row property
    CHECKABLE: an INSERT adds a row, a DELETE removes it, and ``count`` is the live row count.
    """

    def __init__(self):
        self.rows: dict[int, tuple] = {}
        self.inserts: list[tuple] = []
        self._next = 1
        self.deletes: list[int] = []

    def insert(self, params) -> int:
        row_id = self._next
        self._next += 1
        self.rows[row_id] = params
        self.inserts.append(params)
        return row_id

    def delete(self, row_id) -> None:
        self.deletes.append(row_id)
        self.rows.pop(row_id, None)

    @property
    def count(self) -> int:
        return len(self.rows)


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, table):
        self._table = table

    async def execute(self, sql, params=None):
        if "INSERT INTO files" in sql:
            return _Cursor((self._table.insert(params),))
        if "DELETE FROM files" in sql:
            self._table.delete(params[0])
            return _Cursor((None,))
        if "SELECT data FROM files" in sql:
            return _Cursor((self._table.rows.get(params[0]),))
        return _Cursor((None,))


class _ConnCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """The minimum of an async pool the staging path touches: ``.connection()`` and ``.execute``."""

    def __init__(self, table):
        self._table = table

    def connection(self):
        return _ConnCtx(_Conn(self._table))


class NodeDouble:
    """A server's node: it holds the staged ``files`` rows and the bytes a read hands back."""

    def __init__(self, *, master=True, content=b"BODY"):
        self.master = master
        self.guild_id = 7
        self.table = _FilesTable()
        self.apool = _Pool(self.table)
        self.content = content
        self.reads: list[str] = []
        self.removed: list[str] = []
        self.list_calls: list[tuple] = []
        self.list_base = MISSIONS
        self.list_files: list[str] = []
        self.list_error = None

    @property
    def staged(self):
        """Every INSERT's params (the rows ever staged) — what the ``files`` table received."""
        return list(self.table.inserts)

    async def read_file(self, path):
        self.reads.append(path)
        return self.content

    async def remove_file(self, path):
        self.reads.append(path)
        self.removed.append(path)

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        self.list_calls.append((path, pattern, traverse, tuple(ignore or ())))
        if self.list_error is not None:
            raise self.list_error
        return self.list_base, list(self.list_files)


class ServerDouble:
    """A ``Server`` double: records every ``uploadMission`` call and answers the resolver methods."""

    def __init__(self, name="SRS-1", *, master=True, rc=UploadStatus.OK, files=None,
                 current=None, start=1, missions_dir=MISSIONS, configured=None,
                 ignore_dirs=None):
        self.name = name
        self.display_name = name
        self.status = Status.RUNNING
        self.node = NodeDouble(master=master)
        self.rc = rc
        self.files = list(files or [])
        self.current = current
        self.start = start
        self.missions_dir = missions_dir
        self.locals: dict = {}
        if ignore_dirs:
            self.locals["ignore_dirs"] = list(ignore_dirs)
        #: the CONFIGURED ``missionList`` — defaults to the primary spelling of every resolver entry,
        #: so the list action resolves each configured path to the same copy ``files`` already names.
        self.configured = (list(configured) if configured is not None
                           else [logical for logical, _ in self.files])
        self.uploads: list[tuple] = []
        self.loads: list[tuple] = []
        self.adds: list[tuple] = []
        self.deletes: list[tuple] = []
        self.removes: list[str] = []
        self.indexes: list[int] = []
        self.add_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.current_mission = types.SimpleNamespace(filename=current) if current else None

    async def get_missions_dir(self):
        return self.missions_dir

    async def uploadMission(self, filename, url, *, missions_dir=None, force=False):
        self.uploads.append((filename, url, force))
        return self.rc

    async def loadMission(self, mission, modify_mission=True, **kwargs):
        self.loads.append((mission, modify_mission))
        return True

    async def getAllMissionFiles(self):
        return list(self.files)

    async def getMissionList(self):
        return list(self.configured)

    async def getStartIndex(self):
        return self.start

    async def get_current_mission_file(self):
        return self.current

    async def setStartIndex(self, mission_id):
        self.indexes.append(mission_id)

    async def addMission(self, path, *, idx=-1, autostart=False):
        if self.add_error is not None:
            raise self.add_error
        self.adds.append((path, autostart))
        self.configured.append(os.path.normpath(path))
        return list(self.configured)

    async def deleteMission(self, mission_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deletes.append(mission_id)
        self.configured.pop(mission_id - 1)
        return list(self.configured)

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        return await self.node.list_directory(path, pattern=pattern, traverse=traverse, ignore=ignore)


class FakeUpload:
    def __init__(self, data=b"", filename="x.miz"):
        self._data = data
        self.filename = filename

    async def read(self):
        return self._data


class RestDouble:
    """The bits of ``RestAPI`` the endpoint touches: the resolver and the plugin's own node/log."""

    def __init__(self, server):
        self.node = types.SimpleNamespace(master=True)
        self.log = logging.getLogger("test.mission.seam")
        self._server = server

    def get_resolved_server(self, name):
        return name, self._server


# ---------------------------------------------------------------------------------- the harness

@pytest.fixture
def actions():
    """The mission plugin's actions, freshly discovered, with the process registry restored after."""
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    assert {"upload_mission", "download_mission", "get_mission_list"} <= set(registry)
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def ctx_for(server, *, transport: str = "") -> ActionContext:
    bus = types.SimpleNamespace(servers={server.name: server})
    ctx = ActionContext(node=None, bus=bus)
    if transport:
        ctx._transport = transport
    return ctx


async def run(actions, qualname, server, *, transport: str = "", **params):
    return await actions[qualname](ctx_for(server, transport=transport),
                                   server_name=server.name, **params)


# ------------------------------------------------------------- 1. the traversal gate (the action)

@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "../evil.miz",              # a relative escape
    "/tmp/evil.miz",            # an absolute path
    "sub/evil.miz",             # a directory component (posix)
    "sub\\evil.miz",            # a directory component (windows)
    "evil.txt",                 # not a .miz
    "evil",                     # no extension
    "",                         # empty
    ".",                        # the current directory
    "..",                       # the parent directory
])
async def test_upload_refuses_bad_names_and_writes_nothing(actions, name):
    """Every bad name is refused IN the action, with nothing written and nothing staged."""
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name=name, source=b"MISSION")

    assert result.success is False
    assert server.uploads == [], "the ONE writer was never reached"
    assert server.node.staged == [], "no bytes were staged"


@pytest.mark.asyncio
async def test_upload_happy_path_stages_the_bytes_and_passes_the_outcome(actions):
    """The bytes are staged in the ``files`` table and the row id travels to ``uploadMission``."""
    server = ServerDouble(rc=UploadStatus.OK)
    result = await run(actions, "upload_mission", server, name="My Mission.miz",
                       source=b"MISSIONBYTES")

    assert result.success is True
    assert result.upload_status == "OK"
    assert result.filename == "My Mission.miz"
    assert len(server.node.staged) == 1, "the bytes went into the files table"
    params = server.node.staged[0]
    assert params[1] == "My Mission.miz"
    assert params[2].obj == b"MISSIONBYTES"
    # the row id (1) is what travels — the route an agent-hosted server already uses
    assert server.uploads == [("My Mission.miz", 1, False)]


@pytest.mark.asyncio
async def test_upload_stages_the_same_route_for_an_agent_hosted_server(actions):
    """An agent-hosted server travels the SAME files-table route — no second transport exists.

    The row id is what the proxy forwards, so the owning node reads the bytes back by id exactly as
    ``NodeProxy.write_file`` does; a master-hosted server uses the one route too.
    """
    server = ServerDouble(master=False)
    result = await run(actions, "upload_mission", server, name="Remote.miz", source=b"BYTES")

    assert result.success is True
    assert server.node.staged[0][1] == "Remote.miz", "the bytes are staged on the files table"
    assert server.uploads == [("Remote.miz", 1, False)], "the row id travels to the owning node"


@pytest.mark.asyncio
@pytest.mark.parametrize("rc,status", [
    (UploadStatus.FILE_EXISTS, "FILE_EXISTS"),
    (UploadStatus.FILE_IN_USE, "FILE_IN_USE"),
    (UploadStatus.WRITE_ERROR, "WRITE_ERROR"),
])
async def test_upload_passes_the_writers_typed_outcomes_through(actions, rc, status):
    """The bot's own outcome is reported untouched — no invented success rule."""
    server = ServerDouble(rc=rc)
    result = await run(actions, "upload_mission", server, name="Dup.miz", source=b"X")

    assert result.success is False
    assert result.upload_status == status
    assert "Dup.miz" in result.message


# ------------------------------------------------ 1b. the source's trust level (the M1 contract)

class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, chunks, status=200):
        self.status = status
        self.content = _FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _FakeResponse(self._chunks, status=self._status)


@pytest.fixture
def fake_url(monkeypatch):
    """Serve a URL source from an in-process fake, so no test ever touches the network."""
    def install(chunks, status=200):
        import aiohttp
        monkeypatch.setattr(aiohttp, "ClientSession",
                            lambda *args, **kwargs: _FakeSession(list(chunks), status=status))
    return install


@pytest.mark.asyncio
async def test_upload_url_source_is_fetched_capped_and_staged(actions, fake_url):
    """A URL from a privileged caller is FETCHED and STAGED — the row id travels, as for bytes."""
    fake_url([b"URL", b"BYTES"])
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name="Url.miz",
                       source="https://cdn.example/mission.miz", transport=TRANSPORT_DISCORD)

    assert result.success is True
    assert server.uploads == [("Url.miz", 1, False)], "the row id travels, not the URL"
    assert server.node.staged[0][2].obj == b"URLBYTES", "the fetched bytes are what was staged"
    assert server.node.table.count == 0, "the row the fetch staged was cleaned up"


@pytest.mark.asyncio
async def test_upload_refuses_an_over_cap_url_whole(actions, fake_url, monkeypatch):
    """The URL cap is checked AS the bytes arrive: over the cap stages and writes NOTHING."""
    monkeypatch.setattr(sys.modules["plugins.mission.actions"], "MAX_MISSION_UPLOAD_BYTES", 8)
    fake_url([b"x" * 4, b"x" * 5])          # the second chunk crosses the cap
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name="Big.miz",
                       source="https://cdn.example/big.miz", transport=TRANSPORT_DISCORD)

    assert result.success is False
    assert server.uploads == [], "an over-cap URL is refused whole — the writer is never reached"
    assert server.node.staged == []
    assert server.node.table.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [TRANSPORT_WEB, TRANSPORT_PLUGIN, ""])
async def test_upload_refuses_a_path_source_from_a_request_transport(actions, transport):
    """A request (or unnamed) transport may never hand the action a filesystem path."""
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name="Evil.miz",
                       source="/etc/passwd.miz", transport=transport)

    assert result.success is False
    assert "not accepted from this transport" in result.message
    assert server.uploads == [], "the writer was never reached"
    assert server.node.staged == []


@pytest.mark.asyncio
async def test_upload_passes_a_path_source_through_from_a_privileged_caller(actions):
    """A caller that already held the privilege to name a path still hands it to the ONE writer."""
    server = ServerDouble()
    path = "/opt/missions/Hand.miz"
    result = await run(actions, "upload_mission", server, name="Hand.miz", source=path,
                       transport=TRANSPORT_SERVICE)

    assert result.success is True
    assert server.uploads == [("Hand.miz", path, False)]
    assert server.node.staged == [], "a path is never staged"


# -------------------------------------------------- 1c. no orphan files-row (the M1 blocker)

@pytest.mark.asyncio
@pytest.mark.parametrize("rc,status", [
    (UploadStatus.FILE_EXISTS, "FILE_EXISTS"),
    (UploadStatus.FILE_IN_USE, "FILE_IN_USE"),
    (UploadStatus.WRITE_ERROR, "WRITE_ERROR"),
])
async def test_upload_leaves_no_staged_row_on_a_typed_refusal(actions, rc, status):
    """A staged row used to be orphaned on every typed refusal — cleanup is now UNCONDITIONAL."""
    server = ServerDouble(rc=rc)
    result = await run(actions, "upload_mission", server, name="Dup.miz", source=b"X")

    assert result.success is False
    assert result.upload_status == status
    assert server.node.table.count == 0, "the staged row (and its mission bytes) must not survive"
    assert server.node.table.deletes == [1], "the action discarded the row it staged"


@pytest.mark.asyncio
async def test_upload_leaves_no_staged_row_when_the_writer_raises(actions):
    """An exception escaping the ONE writer must not leak the row either."""
    server = ServerDouble()

    async def boom(*args, **kwargs):
        raise RuntimeError("the writer exploded")

    server.uploadMission = boom
    result = await run(actions, "upload_mission", server, name="Raise.miz", source=b"X")

    assert result.success is False
    assert server.node.table.count == 0
    assert server.node.table.deletes == [1]


@pytest.mark.asyncio
async def test_upload_deletes_the_staged_row_exactly_once_on_success(actions):
    """A success deletes its row exactly once: the writer consumes it, the action's cleanup no-ops."""
    server = ServerDouble()

    async def consume(filename, url, *, missions_dir=None, force=False):
        # the ONE writer reads the row and deletes it, exactly as NodeImpl.write_file does
        server.uploads.append((filename, url, force))
        server.node.table.delete(url)
        return UploadStatus.OK

    server.uploadMission = consume
    result = await run(actions, "upload_mission", server, name="Once.miz", source=b"X")

    assert result.success is True
    assert server.node.table.count == 0
    # every DELETE targeted the one staged id — the action's cleanup of an already-consumed row is
    # an idempotent no-op, never a second row and never an error
    assert set(server.node.table.deletes) == {1}


@pytest.mark.asyncio
async def test_upload_refuses_whole_when_over_the_size_cap(actions, monkeypatch):
    """Over the cap means NOTHING is written and NOTHING is staged — never a partial write."""
    monkeypatch.setattr(sys.modules["plugins.mission.actions"], "MAX_MISSION_UPLOAD_BYTES", 8)
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name="Big.miz", source=b"x" * 9)

    assert result.success is False
    assert server.node.staged == []
    assert server.uploads == []


@pytest.mark.asyncio
async def test_upload_allows_exactly_the_cap(actions, monkeypatch):
    """The cap is a maximum, not a strict inequality."""
    monkeypatch.setattr(sys.modules["plugins.mission.actions"], "MAX_MISSION_UPLOAD_BYTES", 8)
    server = ServerDouble()
    result = await run(actions, "upload_mission", server, name="Edge.miz", source=b"x" * 8)

    assert result.success is True
    assert server.uploads == [("Edge.miz", 1, False)]


@pytest.mark.asyncio
async def test_upload_honours_force(actions):
    """``force`` is passed to the ONE writer, not re-implemented."""
    server = ServerDouble()
    await run(actions, "upload_mission", server, name="F.miz", source=b"X", force=True)
    assert server.uploads == [("F.miz", 1, True)]


# ------------------------------------------------------------------- 2. the download resolution

def _newest_files():
    """A list as ``getAllMissionFiles`` returns it: the NEWEST copy already chosen, logical named.

    The LOGICAL name is the ``.dcssb``-stripped path (``utils.logical_mission_path``, the ONE shared
    normaliser ``ServerImpl.getAllMissionFiles`` names its rows with), so a ``.dcssb`` copy reads as
    the plain mission beside its primary.
    """
    return [
        (m("Training.miz"), m("Training.miz")),
        (m("Rotated.miz"), m(".dcssb", "Rotated.miz")),           # .dcssb wins
        (m("Backup.miz"), m(".dcssb", "Backup.miz.orig")),        # .orig wins
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mission", [1, "Training.miz", "Training"])
async def test_download_resolves_the_newest_copy_by_index_or_name(actions, mission):
    """Index (1-based) and name (with or without .miz) both resolve to the primary copy."""
    server = ServerDouble(files=_newest_files())
    result = await run(actions, "download_mission", server, mission=mission)

    assert result.success is True
    assert result.filename == "Training.miz", "the LOGICAL filename is served"
    assert result.content == b"BODY"
    assert server.node.reads == [m("Training.miz")]


@pytest.mark.asyncio
async def test_download_serves_the_dcssb_and_orig_copies_the_resolver_chose(actions):
    """The newest copy — a ``.dcssb`` copy or an ``.orig`` — is what is read, under the logical name."""
    server = ServerDouble(files=_newest_files())

    rotated = await run(actions, "download_mission", server, mission="Rotated.miz")
    assert rotated.success is True
    assert rotated.filename == "Rotated.miz", "the .dcssb marker is stripped from the served name"
    assert server.node.reads[-1] == m(".dcssb", "Rotated.miz")

    backup = await run(actions, "download_mission", server, mission=3)
    assert backup.success is True
    assert backup.filename == "Backup.miz", "the .orig suffix is stripped from the served name"
    assert server.node.reads[-1] == m(".dcssb", "Backup.miz.orig")


@pytest.mark.asyncio
async def test_download_refuses_an_unknown_mission(actions):
    server = ServerDouble(files=_newest_files())
    result = await run(actions, "download_mission", server, mission="Nope.miz")
    assert result.success is False
    assert server.node.reads == []


@pytest.mark.asyncio
async def test_download_refuses_an_out_of_range_index(actions):
    server = ServerDouble(files=_newest_files())
    assert (await run(actions, "download_mission", server, mission=0)).success is False
    assert (await run(actions, "download_mission", server, mission=4)).success is False
    assert server.node.reads == []


# ------------------------------------------------------------------------- 3. the list action

@pytest.mark.asyncio
async def test_mission_list_shape_ordering_and_rotation(actions):
    server = ServerDouble(files=_newest_files(), current=m("Training.miz"), start=2)
    result = await run(actions, "get_mission_list", server)

    assert result.success is True
    assert [m["index"] for m in result.missions] == [1, 2, 3]
    assert [m["name"] for m in result.missions] == ["Training.miz", "Rotated.miz", "Backup.miz"]
    assert [m["copy"] for m in result.missions] == ["primary", "secondary", "orig"]
    assert [m["current"] for m in result.missions] == [True, False, False]
    assert result.data["listStartIndex"] == 2
    assert result.data["current"] == 1
    assert result.data["next"] == 3


@pytest.mark.asyncio
async def test_mission_list_next_wraps(actions):
    server = ServerDouble(files=_newest_files(), start=3)
    result = await run(actions, "get_mission_list", server)
    assert result.data["next"] == 1, "listStartIndex + 1 wraps, exactly as loadNextMission wraps"


@pytest.mark.asyncio
async def test_mission_list_with_no_missions(actions):
    server = ServerDouble(files=[])
    result = await run(actions, "get_mission_list", server)
    assert result.success is True
    assert result.missions == []
    assert result.data["next"] == 0


# --------------------------------------------------------------------- 4. through the REST surface

@pytest.fixture
def rest_bus(monkeypatch):
    """Install a one-server bus so ``ActionContext.from_plugin`` resolves in this process."""
    holder = {}

    def fake_get(cls, t):
        return holder.get("bus") if t is ServiceBus else None

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    return holder


def _rest_with(holder, server) -> RestDouble:
    bus = types.SimpleNamespace(servers={server.name: server})
    holder["bus"] = bus
    return RestDouble(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../evil.miz", "/tmp/evil.miz", "sub/evil.miz", "evil.txt"])
async def test_rest_refuses_traversal_and_writes_nothing(actions, rest_bus, name):
    """Through the REST endpoint the SAME gate refuses, and nothing is written outside missions_dir."""
    server = ServerDouble()
    rest = _rest_with(rest_bus, server)

    with pytest.raises(HTTPException) as caught:
        await RestAPI.mission_upload(rest, server_name="SRS-1", file=FakeUpload(b"X"),
                                     filename=name, load_after=False)

    assert caught.value.status_code == 400
    assert server.uploads == []
    assert server.node.staged == []


@pytest.mark.asyncio
async def test_rest_refuses_a_zero_byte_upload(actions, rest_bus):
    """The REST surface inherits the upload action's empty-content refusal as a 400, never a 200.

    A zero-byte ``.miz`` POSTed to ``/missions`` used to be written and added; the action now refuses
    it in its own sentence, which this endpoint reports as the client error it is.
    """
    server = ServerDouble()
    rest = _rest_with(rest_bus, server)

    with pytest.raises(HTTPException) as caught:
        await RestAPI.mission_upload(rest, server_name="SRS-1", file=FakeUpload(b""),
                                     filename="Empty.miz", load_after=False)

    assert caught.value.status_code == 400
    assert "is empty and was not uploaded" in caught.value.detail
    assert server.uploads == [], "the ONE writer was never reached"
    assert server.node.table.count == 0, "no orphaned files row"


@pytest.mark.asyncio
async def test_rest_happy_path_uses_the_action(actions, rest_bus):
    """A good upload goes through the action and answers this endpoint's own response shape."""
    server = ServerDouble()
    rest = _rest_with(rest_bus, server)

    response = await RestAPI.mission_upload(rest, server_name="SRS-1",
                                           file=FakeUpload(b"MISSIONBYTES"),
                                           filename="Good.miz", load_after=False)

    assert response.status == "success"
    assert "uploaded" in response.message
    assert server.uploads[0][0] == "Good.miz", "the ONE writer saw the validated leaf"


@pytest.mark.asyncio
async def test_rest_load_after_loads_the_uploaded_mission(actions, rest_bus):
    server = ServerDouble()
    rest = _rest_with(rest_bus, server)

    await RestAPI.mission_upload(rest, server_name="SRS-1", file=FakeUpload(b"X"),
                                 filename="Load.miz", load_after=True)

    assert server.loads, "load_after still loads the mission"
    assert server.loads[0][0].endswith("Load.miz")
    assert server.loads[0][1] is False


@pytest.mark.asyncio
async def test_rest_reports_a_file_in_use_as_a_client_error(actions, rest_bus):
    """The writer's typed refusal reaches the caller as a 400, never a 500."""
    server = ServerDouble(rc=UploadStatus.FILE_IN_USE)
    rest = _rest_with(rest_bus, server)

    with pytest.raises(HTTPException) as caught:
        await RestAPI.mission_upload(rest, server_name="SRS-1", file=FakeUpload(b"X"),
                                     filename="Busy.miz", load_after=False)

    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_rest_load_after_loads_the_validated_name_not_the_form_leaf(actions, rest_bus,
                                                                          monkeypatch):
    """``load_after`` must target what was WRITTEN (the action's ``result.filename``), not the form.

    If sanitisation alters the leaf, loading the RAW form name would target a file that does not
    exist. On POSIX the only character ``utils.sanitize_filename`` strips is ``/`` — which the upload
    gate already refuses — so no gate-passing name is altered on Linux; the test installs a sanitiser
    that alters the leaf (dropping a space), exactly as a wider illegal-char set would, and proves
    the endpoint follows ``result.filename``.
    """
    import os

    import plugins.mission.actions as mission_actions

    real = mission_actions.utils.sanitize_filename

    def altering(filename, base_directory, **kwargs):
        return real(str(filename).replace("Sanitise Me", "Sanitised"), base_directory, **kwargs)

    monkeypatch.setattr(mission_actions.utils, "sanitize_filename", altering)

    server = ServerDouble()
    rest = _rest_with(rest_bus, server)
    await RestAPI.mission_upload(rest, server_name="SRS-1", file=FakeUpload(b"X"),
                                 filename="Sanitise Me.miz", load_after=True)

    assert server.uploads[0][0] == "Sanitised.miz", "the ONE writer saw the sanitised leaf"
    assert server.loads, "load_after still loads the mission"
    assert server.loads[0][0] == os.path.normpath(os.path.join(server.missions_dir, "Sanitised.miz"))
    assert server.loads[0][0] != os.path.normpath(os.path.join(server.missions_dir, "Sanitise Me.miz")), \
        "the raw form leaf must not be the load target"


# --------------------------------------- 5. M3: the CONFIGURED list, and its add / delete / load

def _configured_fixture():
    """Five ``.miz`` files on disk (as ``getAllMissionFiles`` would name them), only TWO configured.

    This is the exact shape of the operator's bug card: the missions directory holds more mission files than
    the configured ``missionList``, and only the CONFIGURED ones may appear.
    """
    files = [
        (m("Training.miz"), m("Training.miz")),
        (m("Rotation.miz"), m(".dcssb", "Rotation.miz")),          # .dcssb wins
        (m("Extra1.miz"), m("Extra1.miz")),
        (m("Extra2.miz"), m("Extra2.miz")),
        (m("Extra3.miz"), m("Extra3.miz")),
    ]
    configured = [m("Training.miz"), m("Rotation.miz")]
    return files, configured


@pytest.mark.asyncio
async def test_the_list_shows_only_the_configured_missions(actions):
    """THE BUG: more files on disk than configured — the list must show only the configured ones."""
    files, configured = _configured_fixture()
    server = ServerDouble(files=files, configured=configured)
    result = await run(actions, "get_mission_list", server)

    assert [m["name"] for m in result.missions] == ["Training.miz", "Rotation.miz"]
    assert len(result.missions) == 2, "the three unconfigured files on disk must not appear"
    assert [m["copy"] for m in result.missions] == ["primary", "secondary"]


@pytest.mark.asyncio
async def test_a_configured_mission_whose_file_is_gone_is_shown_missing(actions):
    """An unresolvable configured entry stays in the list, tagged ``missing`` — never dropped."""
    server = ServerDouble(files=[], configured=[m("Gone.miz")])
    result = await run(actions, "get_mission_list", server)

    assert result.success is True
    assert len(result.missions) == 1
    row = result.missions[0]
    assert row["name"] == "Gone.miz"
    assert row["copy"] == "missing"
    assert row["resolved"] is False


@pytest.mark.asyncio
async def test_download_follows_the_configured_order(actions):
    """The download names a mission by the CONFIGURED 1-based index — an unconfigured file is not it."""
    files, configured = _configured_fixture()
    server = ServerDouble(files=files, configured=configured)

    result = await run(actions, "download_mission", server, mission=2)
    assert result.success is True
    assert result.filename == "Rotation.miz"
    assert server.node.reads[-1] == m(".dcssb", "Rotation.miz")

    assert (await run(actions, "download_mission", server, mission=3)).success is False


@pytest.mark.asyncio
async def test_the_add_picker_excludes_installed_missions_both_spellings(actions):
    """The picker drops a file already in the list, checked as the plain path AND its ``.dcssb`` sibling."""
    server = ServerDouble(configured=[m("Training.miz"), m(".dcssb", "Only.miz")])
    server.node.list_files = [m("Training.miz"), m("Only.miz"), m("New.miz")]
    server.node.list_base = MISSIONS

    result = await run(actions, "get_addable_missions", server)
    paths = [m["path"] for m in result.missions]
    assert paths == ["New.miz"], paths
    assert server.node.list_calls[-1][1] == ["*.miz", "*.sav"], "the picker's own pattern"
    assert ".dcssb" in server.node.list_calls[-1][3], "the .dcssb directory is always ignored"


@pytest.mark.asyncio
async def test_the_add_picker_honours_ignore_dirs(actions):
    """A configured ``ignore_dirs`` rides the node listing, exactly as ``mizfile_autocomplete`` sends it."""
    server = ServerDouble(configured=[], ignore_dirs=["Backups"])
    server.node.list_files = []
    await run(actions, "get_addable_missions", server)
    assert "Backups" in server.node.list_calls[-1][3]


@pytest.mark.asyncio
async def test_add_with_autostart_and_add_then_load(actions):
    """``autostart`` is passed to ``addMission`` (loaded at next start); ``load`` loads the 1-based index."""
    server = ServerDouble(configured=[])
    result = await run(actions, "add_mission", server, path="New.miz", autostart=True)
    assert result.success is True
    assert server.adds == [(m("New.miz"), True)], "autostart rides addMission"
    assert result.data["index"] == 1

    loaded = ServerDouble(configured=[])
    result2 = await run(actions, "add_mission", loaded, path="New.miz", load=True)
    assert result2.success is True
    assert loaded.loads and loaded.loads[0][0] == 1, "the 1-based index is loaded"
    assert result2.data["loaded"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../evil.miz", "/abs/evil.miz", "evil.txt", ""])
async def test_add_refuses_a_bad_path(actions, bad):
    server = ServerDouble(configured=[])
    result = await run(actions, "add_mission", server, path=bad)
    assert result.success is False
    assert server.adds == [], "nothing was added"


@pytest.mark.asyncio
async def test_remove_refuses_the_running_mission(actions):
    """The running-mission refusal is the DISCORD COMMAND's sentence, and nothing is deleted.

    ``plugins/mission/commands.py::/mission delete`` answers "You can't delete the running mission."
    — the console pre-check says the SAME words, so one rule reads as one message on both surfaces.
    """
    server = ServerDouble(files=[(m("Training.miz"), m("Training.miz"))],
                          configured=[m("Training.miz")], current=m("Training.miz"))
    result = await run(actions, "delete_mission", server, mission=1)
    assert result.success is False
    assert result.message == "You can't delete the running mission."
    assert server.deletes == []


@pytest.mark.asyncio
async def test_remove_from_list_and_from_disk(actions):
    """Remove takes the 1-based index out of the list; ``delete_from_disk`` removes all three copies."""
    server = ServerDouble(files=[(m("Training.miz"), m("Training.miz"))],
                          configured=[m("Training.miz"), m("Other.miz")])
    result = await run(actions, "delete_mission", server, mission=2, delete_from_disk=True)
    assert result.success is True
    assert server.deletes == [2]
    assert result.data["deleted_from_disk"] is True
    assert m("Other.miz") in server.node.removed
    assert m(".dcssb", "Other.miz") in server.node.removed
    assert m(".dcssb", "Other.miz.orig") in server.node.removed
    # the EXACT order of ``plugins/mission/commands.py::/mission delete``: the configured file first,
    # then the derived ``.dcssb`` sibling, then its ``.orig`` — so the two cannot drift.
    assert server.node.removed == [
        m("Other.miz"), m(".dcssb", "Other.miz"), m(".dcssb", "Other.miz.orig")]


@pytest.mark.asyncio
async def test_remove_from_disk_uses_the_commands_order_for_a_secondary_entry(actions):
    """A ``.dcssb``-spelled configured entry removes THAT copy first, then the primary, then ``.orig``.

    The same statements in the same order as the Discord command's ``logical_mission_path`` branch
    (``.dcssb`` is a COMPONENT, never a substring) — the two surfaces cannot drift about what
    "delete from disk" touches, or in what order.
    """
    server = ServerDouble(files=[(m("Other.miz"), m(".dcssb", "Other.miz"))],
                          configured=[m(".dcssb", "Other.miz")])
    result = await run(actions, "delete_mission", server, mission=1, delete_from_disk=True)
    assert result.success is True
    assert server.node.removed == [
        m(".dcssb", "Other.miz"), m("Other.miz"), m(".dcssb", "Other.miz.orig")]


@pytest.mark.asyncio
async def test_remove_surfaces_the_bots_own_running_refusal(actions):
    """The bot's ``AttributeError`` wording reaches the caller verbatim, never reworded."""
    server = ServerDouble(configured=[m("Training.miz")])
    server.delete_error = AttributeError("Can't delete the running mission!")
    result = await run(actions, "delete_mission", server, mission=1)
    assert result.success is False
    assert result.message == "Can't delete the running mission!"


@pytest.mark.asyncio
async def test_upload_with_autostart_arms_and_with_load_loads(actions):
    """The upload's two load options: ``autostart`` arms ``listStartIndex``; ``load`` loads now."""
    armed = ServerDouble(configured=[m("New.miz")])
    result = await run(actions, "upload_mission", armed, name="New.miz", source=b"X", autostart=True)
    assert result.success is True
    assert armed.indexes == [1], "setStartIndex armed the rotation to the uploaded mission"
    assert "next start" in result.message

    loaded = ServerDouble(configured=[m("New.miz")])
    result2 = await run(actions, "upload_mission", loaded, name="New.miz", source=b"X", load=True)
    assert result2.success is True
    assert loaded.loads and loaded.loads[0][0] == 1
    assert "loaded now" in result2.message


@pytest.mark.asyncio
async def test_a_zero_byte_upload_is_refused_with_its_own_sentence(actions):
    """A 0-byte ``.miz`` is not a mission: the upload refuses it BEFORE staging OR writing.

    The mirror of the download side's "answered with no file content": an empty payload gets the
    upload action's OWN sentence, nothing is staged (no ``files`` row) and ``uploadMission`` — the
    ONE writer — is never called.
    """
    server = ServerDouble(configured=[])
    result = await run(actions, "upload_mission", server, name="Empty.miz", source=b"")

    assert result.success is False
    assert result.message == "The mission file 'Empty.miz' is empty and was not uploaded."
    assert server.uploads == [], "the writer must never see an empty mission"
    assert server.node.staged == [], "nothing may be staged for an empty mission"
    assert server.node.table.count == 0, "no orphaned files row"


# ------------------------------------------- the busy refusal on the mission-list writes
#
# A writer's ``RuntimeError`` IS the refusal sentence (the mission-list guard in
# ``core.data.impl.serverimpl``), so the action hands it back verbatim and writes NO traceback:
# an ordinary refusal is not a failure. Every other exception keeps the wrapped path and the log.

@pytest.mark.asyncio
async def test_add_mission_refusal_is_the_sentence_and_logs_no_traceback(actions, caplog):
    server = ServerDouble(configured=[])
    server.add_error = RuntimeError(BOT_REFUSAL)

    with caplog.at_level(logging.DEBUG):
        result = await run(actions, "add_mission", server, path="New.miz")

    assert result.success is False
    assert result.message == BOT_REFUSAL, "the sentence verbatim, not a prefix or a wrap"
    assert server.adds == []
    assert not [record for record in caplog.records if record.exc_info], \
        "a normal refusal must not write a traceback"


@pytest.mark.asyncio
async def test_add_mission_other_errors_keep_the_wrapped_path_and_the_log(actions, caplog):
    """CONTROL: a non-refusal exception still takes the wrapped sentence with a logged traceback."""
    server = ServerDouble(configured=[])
    server.add_error = ValueError("boom")

    with caplog.at_level(logging.DEBUG):
        result = await run(actions, "add_mission", server, path="New.miz")

    assert result.success is False
    assert result.message == "Failed to add mission 'New.miz': boom"
    assert [record for record in caplog.records if record.exc_info], \
        "a genuine failure keeps its traceback in the log"


@pytest.mark.asyncio
async def test_delete_mission_refusal_is_the_sentence_and_logs_no_traceback(actions, caplog):
    server = ServerDouble(configured=[m("Training.miz")])
    server.delete_error = RuntimeError(BOT_REFUSAL)

    with caplog.at_level(logging.DEBUG):
        result = await run(actions, "delete_mission", server, mission=1)

    assert result.success is False
    assert result.message == BOT_REFUSAL, "the sentence verbatim, not a prefix or a wrap"
    assert server.deletes == []
    assert not [record for record in caplog.records if record.exc_info], \
        "a normal refusal must not write a traceback"


@pytest.mark.asyncio
async def test_delete_mission_other_errors_keep_the_wrapped_path_and_the_log(actions, caplog):
    """CONTROL: a non-refusal exception still takes the wrapped sentence with a logged traceback."""
    server = ServerDouble(configured=[m("Training.miz")])
    server.delete_error = ValueError("boom")

    with caplog.at_level(logging.DEBUG):
        result = await run(actions, "delete_mission", server, mission=1)

    assert result.success is False
    assert result.message == "Failed to delete mission 'Training.miz': boom"
    assert [record for record in caplog.records if record.exc_info]


@pytest.mark.asyncio
async def test_bulk_delete_carries_the_refusal_and_logs_no_traceback(actions, caplog):
    server = ServerDouble(configured=[m("Training.miz"), m("Other.miz")])
    server.delete_error = RuntimeError(BOT_REFUSAL)

    with caplog.at_level(logging.DEBUG):
        result = await run(actions, "delete_missions", server, missions=[1, 2])

    assert result.success is False
    assert "No mission could be removed" in result.message
    assert result.message.count(BOT_REFUSAL) == 2, "each per-mission refusal carries the sentence"
    assert not [record for record in caplog.records if record.exc_info], \
        "a normal refusal must not write a traceback"
