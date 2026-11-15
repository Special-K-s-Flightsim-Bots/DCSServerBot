"""Pins for the node-aware ModManager surface.

The package folder of a ``folder`` (``RootFolder`` / ``SavedGames``) belongs to the node that executes the
work, so the reads and writes that touch it must answer on that node. These tests drive the service with a
fake node and a recording bus, and assert both the wire call (a remote node) and the file effects (a local
node).
"""
from __future__ import annotations

import logging
import os
import types
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from core.data.dataobject import DataObject
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------------------------- doubles


class _Node(DataObject):
    """A node double carrying a name; the proxy decorator serialises it by name."""

    def __init__(self, name: str, *, remote: bool):
        super().__init__(name=name, node=types.SimpleNamespace(pool=None, apool=None, config=None))
        self.is_remote = remote


class Bus:
    """Records every RPC the proxy decorator sends and returns a preset answer."""

    def __init__(self, answer=None):
        self.calls: list[tuple[dict, str | None, float | None]] = []
        self.answer = answer

    async def send_to_node_sync(self, message: dict, *, node: str | None = None, timeout=None):
        self.calls.append((message, node, timeout))
        return self.answer


class Cursor:
    """Records every statement the service executes, with its parameters."""

    def __init__(self):
        self.statements: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return self


class _Connection:
    def __init__(self, cursor: Cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        return False


class Pool:
    """An ``apool`` double: ``async with pool.connection() as conn`` yields the recording cursor."""

    def __init__(self, cursor: Cursor):
        self._cursor = cursor

    def connection(self):
        return _Connection(self._cursor)


def _service(*, packages: dict, bus: Bus | None = None, node: _Node | None = None,
             cursor: Cursor | None = None) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry)."""
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger("tests.modmanager")
    service.node = node or _Node("node-local", remote=False)
    service.bus = bus if bus is not None else Bus()
    service.locals = {DEFAULT_TAG: dict(packages)}
    service._config = {}
    service.apool = Pool(cursor) if cursor is not None else None
    service.temp_packages = []
    return service


def _package_folder(root: Path) -> None:
    """One folder holding a zip package, a directory package and three entries that must stay absent."""
    (root / "A4E-C_v2.1.0.zip").write_bytes(b"PK\x03\x04placeholder")
    directory = root / "SRS_v1.5.2"
    directory.mkdir()
    (directory / "x.txt").write_bytes(b"12345")
    # a dotted leftover that would parse: only the leading-dot filter can drop it
    (root / ".SRS_v0.9.0.zip").write_bytes(b"old")
    (root / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    (root / "notes.txt").write_text("not a package", encoding="utf-8")


def _write_backup(folder: Path, name: str, package_name: str, version: str, log: str) -> Path:
    backup = folder / name / f"{package_name}_v{version}"
    backup.mkdir(parents=True)
    (backup / "install.log").write_text(log, encoding="utf-8")
    return backup


# ------------------------------------------------------------------- list_available_packages (local)


@pytest.mark.asyncio
async def test_list_available_packages_reports_a_zip_and_a_directory_package(tmp_path):
    root = tmp_path / "RootFolder"
    root.mkdir()
    _package_folder(root)
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})

    entries = await service.list_available_packages(_Node("node-local", remote=False), Folder.RootFolder)

    by_name = {entry["name"]: entry for entry in entries}
    assert set(by_name) == {"A4E-C", "SRS"}
    assert by_name["A4E-C"]["kind"] == "zip"
    assert by_name["A4E-C"]["version"] == "2.1.0"
    assert by_name["A4E-C"]["size"] == os.path.getsize(root / "A4E-C_v2.1.0.zip")
    assert by_name["SRS"]["kind"] == "dir"
    assert by_name["SRS"]["version"] == "1.5.2"
    assert by_name["SRS"]["size"] == 5
    assert all(isinstance(entry["mtime"], float) for entry in entries)


@pytest.mark.asyncio
async def test_list_available_packages_accepts_the_folder_name_and_reports_an_empty_folder(tmp_path):
    root = tmp_path / "SavedGames"
    root.mkdir()
    _package_folder(root)
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(root)})

    entries = await service.list_available_packages(_Node("node-local", remote=False), "SavedGames")
    assert {entry["name"] for entry in entries} == {"A4E-C", "SRS"}

    empty = tmp_path / "empty"
    empty.mkdir()
    missing_service = _service(packages={"RootFolder": str(tmp_path / "absent"), "SavedGames": str(empty)})
    assert await missing_service.list_available_packages(
        _Node("node-local", remote=False), Folder.RootFolder) == []


# ------------------------------------------------------------- list_available_packages (remote)


@pytest.mark.asyncio
async def test_list_available_packages_on_a_remote_node_never_reads_the_local_disk(tmp_path, monkeypatch):
    root = tmp_path / "RootFolder"
    root.mkdir()
    _package_folder(root)
    seen: list[str] = []
    real_listdir = os.listdir

    def spy(path="."):
        seen.append(str(path))
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", spy)
    bus = Bus(answer=[{"name": "A4E-C", "version": "2.1.0", "kind": "zip", "size": 1, "mtime": 1.0}])
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)}, bus=bus)

    answer = await service.list_available_packages(_Node("node-eu", remote=True), Folder.RootFolder)

    assert seen == [], "the remote call must not read the calling process's disk"
    assert answer == bus.answer
    assert len(bus.calls) == 1
    message, node_name, _timeout = bus.calls[0]
    assert message["command"] == "rpc"
    assert message["service"] == "ModManagerService"
    assert message["method"] == "list_available_packages"
    assert message["params"] == {"node": "node-eu", "folder": "RootFolder"}
    assert node_name == "node-eu"


# ------------------------------------------------------------------------- list_package_versions


@pytest.mark.asyncio
async def test_list_package_versions_answers_every_package_of_one_call(tmp_path):
    """One read answers the whole table: versions, the newest of them and whether it is on disk."""
    root = tmp_path / "RootFolder"
    root.mkdir()
    (root / "A4E-C_v2.1.0.zip").write_bytes(b"x")
    (root / "A4E-C_v2.2.0.zip").write_bytes(b"y")
    (root / "SRS_v1.5.2").mkdir()
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})

    answer = await service.list_package_versions(
        _Node("node-local", remote=False), Folder.RootFolder, ["A4E-C", "SRS", "Absent"])

    assert set(answer) == {"A4E-C", "SRS", "Absent"}
    assert answer["A4E-C"]["versions"] == ["2.1.0", "2.2.0"]
    assert answer["A4E-C"]["latest"] == "2.2.0"
    assert answer["A4E-C"]["downloaded"] is True
    assert answer["A4E-C"]["repo"] == ""
    assert answer["SRS"]["versions"] == ["1.5.2"]
    assert answer["SRS"]["latest"] == "1.5.2"
    assert answer["Absent"] == {"versions": [], "latest": "", "repo": "", "downloaded": False}


@pytest.mark.asyncio
async def test_a_declared_repository_version_absent_from_the_folder_is_reported_for_download(
        tmp_path, monkeypatch):
    """A release the repository publishes but the folder does not hold is named, not counted as on disk."""
    root = tmp_path / "RootFolder"
    root.mkdir()
    (root / "A4E-C_v2.1.0.zip").write_bytes(b"x")
    service = _service(packages={
        "RootFolder": str(root), "SavedGames": str(tmp_path),
        "packages": [{"name": "A4E-C", "version": "latest", "source": "RootFolder",
                      "repo": "https://github.com/heclak/community-a4e-c"}]})

    async def releases(repo):
        return {"2.1.0", "2.3.0"}

    async def newest(repo):
        return "2.3.0"

    monkeypatch.setattr(service, "get_repo_versions", releases)
    monkeypatch.setattr(service, "get_latest_repo_version", newest)

    answer = await service.list_package_versions(
        _Node("node-local", remote=False), Folder.RootFolder, ["A4E-C"])

    assert answer["A4E-C"]["versions"] == ["2.1.0", "2.3.0"]
    assert answer["A4E-C"]["latest"] == "2.3.0"
    assert answer["A4E-C"]["repo"] == "https://github.com/heclak/community-a4e-c"
    assert answer["A4E-C"]["downloaded"] is False


@pytest.mark.asyncio
async def test_get_available_versions_accepts_a_node_reference(tmp_path):
    """The version list is readable for a node-owned folder, where there is no server to name."""
    root = tmp_path / "RootFolder"
    root.mkdir()
    (root / "A4E-C_v2.1.0.zip").write_bytes(b"x")
    (root / "A4E-C_v2.0.0.zip").write_bytes(b"y")
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})

    assert await service.get_available_versions(
        _Node("node-local", remote=False), Folder.RootFolder, "A4E-C") == ["2.0.0", "2.1.0"]


@pytest.mark.asyncio
async def test_list_package_versions_on_a_remote_node_never_reads_the_local_disk(tmp_path, monkeypatch):
    """The batched read is one RPC naming the node, the folder and every package."""
    root = tmp_path / "RootFolder"
    root.mkdir()
    _package_folder(root)
    seen: list[str] = []
    real_listdir = os.listdir

    def spy(path="."):
        seen.append(str(path))
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", spy)
    bus = Bus(answer={"A4E-C": {"versions": ["2.1.0"], "latest": "2.1.0", "repo": "",
                                "downloaded": True}})
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)}, bus=bus)

    answer = await service.list_package_versions(
        _Node("node-eu", remote=True), Folder.RootFolder, ["A4E-C", "SRS"])

    assert seen == [], "the remote call must not read the calling process's disk"
    assert answer == bus.answer
    assert len(bus.calls) == 1
    message, node_name, _timeout = bus.calls[0]
    assert message["command"] == "rpc"
    assert message["service"] == "ModManagerService"
    assert message["method"] == "list_package_versions"
    assert message["params"] == {"node": "node-eu", "folder": "RootFolder",
                                 "package_names": ["A4E-C", "SRS"]}
    assert node_name == "node-eu"


# ------------------------------------------------------------------------- download_package


@pytest.mark.asyncio
async def test_download_package_on_a_remote_node_names_the_node_and_the_folder(tmp_path):
    bus = Bus(answer=True)
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(tmp_path)}, bus=bus)

    answer = await service.download_package(
        _Node("node-eu", remote=True), Folder.SavedGames,
        "https://github.com/heclak/community-a4e-c", version="2.1.0", package_name="A4E-C", force=True)

    assert answer is True
    message, node_name, _timeout = bus.calls[0]
    assert message["command"] == "rpc"
    assert message["service"] == "ModManagerService"
    assert message["method"] == "download_package"
    assert message["params"] == {"node": "node-eu", "folder": "SavedGames",
                                 "url": "https://github.com/heclak/community-a4e-c",
                                 "version": "2.1.0", "package_name": "A4E-C", "force": True}
    assert node_name == "node-eu"


@pytest.mark.asyncio
async def test_download_package_routes_a_repo_url_and_a_plain_url_and_refuses_an_ambiguous_repo(tmp_path):
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(tmp_path)})
    calls: list[tuple] = []

    async def fake_repo(repo, folder, *, package_name=None, version=None, force=False):
        calls.append(("repo", repo, folder, package_name, version, force))
        return ["https://example.invalid/a.zip", "https://example.invalid/b.zip"]

    async def fake_download(url, folder, version, *, force=False):
        calls.append(("download", url, folder, version, force))

    service.download_from_repo = fake_repo
    service.download = fake_download
    node = _Node("node-local", remote=False)

    assert await service.download_package(node, Folder.SavedGames,
                                          "https://github.com/heclak/community-a4e-c",
                                          version="2.1.0") is False
    assert calls == [("repo", "https://github.com/heclak/community-a4e-c", Folder.SavedGames,
                      None, "2.1.0", False)]

    calls.clear()
    assert await service.download_package(node, Folder.SavedGames,
                                          "https://example.com/Foo_v1.0.zip", version="1.0") is True
    assert calls == [("download", "https://example.com/Foo_v1.0.zip", Folder.SavedGames, "1.0", False)]


@pytest.mark.asyncio
async def test_download_package_reports_a_single_asset_repo_download_as_success(tmp_path):
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(tmp_path)})

    async def fake_repo(repo, folder, *, package_name=None, version=None, force=False):
        return None

    service.download_from_repo = fake_repo
    assert await service.download_package(_Node("node-local", remote=False), Folder.SavedGames,
                                          "https://github.com/heclak/community-a4e-c") is True


# --------------------------------------------------------- root install/uninstall on a remote node


@pytest.mark.asyncio
async def test_install_root_package_on_a_remote_node_goes_through_the_bus(tmp_path):
    bus = Bus(answer=True)
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(tmp_path)}, bus=bus)

    answer = await service.install_root_package(_Node("node-eu", remote=True), "A4E-C", "2.1.0")

    assert answer is True
    message, node_name, _timeout = bus.calls[0]
    assert message["method"] == "install_root_package"
    assert message["params"] == {"node": "node-eu", "package_name": "A4E-C", "version": "2.1.0"}
    assert node_name == "node-eu"


@pytest.mark.asyncio
async def test_uninstall_root_package_on_a_remote_node_goes_through_the_bus(tmp_path):
    bus = Bus(answer=True)
    service = _service(packages={"RootFolder": str(tmp_path), "SavedGames": str(tmp_path)}, bus=bus)

    answer = await service.uninstall_root_package(_Node("node-eu", remote=True), "A4E-C", "2.1.0")

    assert answer is True
    message, node_name, _timeout = bus.calls[0]
    assert message["method"] == "uninstall_root_package"
    assert message["params"] == {"node": "node-eu", "package_name": "A4E-C", "version": "2.1.0",
                                 "force": False}
    assert node_name == "node-eu"


@pytest.mark.asyncio
async def test_a_local_root_uninstall_inside_the_node_stays_in_process(tmp_path):
    root = tmp_path / "RootFolder"
    root.mkdir()
    installation = tmp_path / "dcs"
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _write_backup(root, ".node-local", "SRS", "1.5.2", "w Mods/x.lua\n")

    bus = Bus()
    cursor = Cursor()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)}, bus=bus, cursor=cursor)
    server = types.SimpleNamespace(name="SRS-1", node=node, is_remote=False)

    assert await service.uninstall_package(server, Folder.RootFolder, "SRS", "1.5.2") is True

    assert bus.calls == [], "the root uninstall runs on the node, not over the bus"
    assert not (installation / "Mods" / "x.lua").exists()
    assert cursor.statements and "DELETE FROM mm_packages" in cursor.statements[-1][0]


@pytest.mark.asyncio
async def test_a_root_install_inside_the_node_stays_in_process(tmp_path):
    import shutil

    root = tmp_path / "RootFolder"
    root.mkdir()
    package = tmp_path / "src" / "SRS_v1.5.2.zip"
    package.parent.mkdir()
    with zipfile.ZipFile(package, "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    shutil.copy(package, root / package.name)
    installation = tmp_path / "dcs"
    installation.mkdir()

    bus = Bus()
    cursor = Cursor()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)}, bus=bus, cursor=cursor)

    assert await service.install_root_package(node, "SRS", "1.5.2") is True

    assert bus.calls == []
    assert (installation / "Mods" / "x.lua").exists()
    assert (root / ".node-local" / "SRS_v1.5.2" / "install.log").exists()


# ------------------------------------------------------------------------------ the UTC stamp


@pytest.mark.asyncio
async def test_do_install_writes_a_utc_timestamp_instead_of_the_column_default(tmp_path):
    root = tmp_path / "RootFolder"
    root.mkdir()
    package = root / "SRS_v1.5.2.zip"
    with zipfile.ZipFile(package, "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    installation = tmp_path / "dcs"
    installation.mkdir()

    cursor = Cursor()
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)}, cursor=cursor)
    node = types.SimpleNamespace(name="node-local", installation=str(installation))

    assert await service.do_install(node, Folder.RootFolder, "SRS", "1.5.2", str(root), str(package)) is True

    statement, params = cursor.statements[-1]
    collapsed = " ".join(statement.split())
    assert "INSERT INTO mm_packages (node, package_name, version, folder, time)" in collapsed
    assert "timezone('UTC', now())" in collapsed
    assert params == ("node-local", "SRS", "1.5.2", "RootFolder")


# --------------------------------------- the folder a DELETE names, and the name a download writes


@pytest.mark.asyncio
async def test_a_local_root_uninstall_deletes_with_the_folder_value(tmp_path):
    """The bookkeeping row is matched on the folder's WIRE value, not on the enum member.

    ``mm_packages.folder`` carries the two words (``RootFolder`` / ``SavedGames``), so a statement
    bound with the member only matches while the driver happens to render it as its name.
    """
    root = tmp_path / "RootFolder"
    root.mkdir()
    installation = tmp_path / "dcs"
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _write_backup(root, ".node-local", "SRS", "1.5.2", "w Mods/x.lua\n")

    cursor = Cursor()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)},
                       cursor=cursor)
    server = types.SimpleNamespace(name="SRS-1", node=node, is_remote=False)

    assert await service.uninstall_package(server, Folder.RootFolder, "SRS", "1.5.2") is True

    statement, params = cursor.statements[-1]
    assert "mm_packages" in statement and "DELETE" in statement
    assert params == ("node-local", "RootFolder", "SRS", "1.5.2")


class _DownloadResponse:
    """A stand-in for one ``aiohttp`` response: a body, and nothing to raise."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def read(self) -> bytes:
        return self._body


class _DownloadSession:
    """A stand-in for ``aiohttp.ClientSession`` answering one body for every request."""

    body = b"PK\x03\x04placeholder"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        return _DownloadResponse(self.body)


@pytest.mark.asyncio
async def test_a_url_whose_name_is_a_location_is_refused_before_the_write(tmp_path, monkeypatch):
    """A URL whose own file name resolves outside the folder is refused by the rule, not by ``open``."""
    from services.modmanager import service as service_module

    monkeypatch.setattr(service_module, "ClientSession", _DownloadSession)
    root = tmp_path / "RootFolder"
    root.mkdir()
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})
    service.node.proxy = None
    service.node.proxy_auth = None

    with pytest.raises(ValueError, match="does not name a file in"):
        await service.download("https://example.invalid/packages/..", Folder.RootFolder, None, force=True)

    assert os.listdir(root) == [], "a refused download left something in the package folder"


@pytest.mark.asyncio
async def test_a_plain_url_download_writes_a_file_the_listing_recognises(tmp_path, monkeypatch):
    """The file a plain URL leaves behind parses back through the service's own reader."""
    from services.modmanager import service as service_module

    monkeypatch.setattr(service_module, "ClientSession", _DownloadSession)
    root = tmp_path / "RootFolder"
    root.mkdir()
    service = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})
    # the download reads the proxy off the service's OWN node, the one that runs it
    service.node.proxy = None
    service.node.proxy_auth = None
    node = _Node("node-local", remote=False)
    url = "https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip"

    assert await service.download_package(node, Folder.RootFolder, url, version="2.1.2") is True

    written = sorted(os.listdir(root))
    assert written == ["SRS_Radio_Standalone_v2.1.2.zip"]
    assert ModManagerService.parse_filename(written[0]) == ("SRS_Radio_Standalone", "2.1.2")
    entries = await service.list_available_packages(node, Folder.RootFolder)
    assert [(entry["name"], entry["version"]) for entry in entries] == [
        ("SRS_Radio_Standalone", "2.1.2")]
