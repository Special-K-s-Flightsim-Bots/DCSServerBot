"""Pins for the mod gate: an install or removal may not rewrite game files under a live process.

``RootFolder`` is the node's DCS installation, so every server on that node must be down; ``SavedGames`` is
one instance, so only that server must be down. The gate lives inside the service methods, which means it
reads the registry of whichever process executes the write: the node itself for a local call, the master for
a call it filters by node name.
"""
from __future__ import annotations

import asyncio
import logging
import types
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from core.data.const import Status
    from core.data.dataobject import DataObject
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)


LOG = "tests.modmanager.gate"


# --------------------------------------------------------------------------------------------- doubles


class _Node(DataObject):
    """A node double carrying a name; the proxy decorator serialises it by name."""

    def __init__(self, name: str, *, remote: bool):
        super().__init__(name=name, node=types.SimpleNamespace(pool=None, apool=None, config=None))
        self.is_remote = remote
        self.installation = ""


def _server(name: str, node: _Node, status: Status, *, home: str = "",
            process: bool = False, probe_raises: bool = False) -> types.SimpleNamespace:
    """A server double: the gate reads its ``status``, the write path reads its ``node``/``instance``.

    ``process`` is what ``is_running()`` answers: whether a DCS process of that instance exists. It is the
    only evidence an ``UNREGISTERED`` record leaves about the folder. ``probe_raises`` makes that probe
    fail instead of answering.
    """

    async def is_running() -> bool:
        if probe_raises:
            raise OSError("the process probe is unavailable")
        return process

    return types.SimpleNamespace(
        name=name,
        status=status,
        node=node,
        is_running=is_running,
        is_remote=node.is_remote,
        instance=types.SimpleNamespace(name=name, home=home),
    )


class Bus:
    """The process registry plus a recorder for the RPCs the proxy decorator would send."""

    def __init__(self, servers: dict[str, types.SimpleNamespace] | None = None):
        self.servers = servers if servers is not None else {}
        self.calls: list[tuple[dict, str | None, float | None]] = []
        self.answer = True

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
    def __init__(self, cursor: Cursor):
        self._cursor = cursor

    def connection(self):
        return _Connection(self._cursor)


def _service(root: Path, saved_games: Path, servers: dict | None = None) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry)."""
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger(LOG)
    service.node = _Node("node-local", remote=False)
    service.bus = Bus(servers)
    service.locals = {DEFAULT_TAG: {"RootFolder": str(root), "SavedGames": str(saved_games)}}
    service._config = {}
    service.apool = Pool(Cursor())
    service.temp_packages = []
    return service


def _root_zip(root: Path, package_name: str = "SRS", version: str = "1.5.2") -> Path:
    """A one-entry zip package in the RootFolder package directory."""
    package = root / f"{package_name}_v{version}.zip"
    with zipfile.ZipFile(package, "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    return package


def _backup(folder: Path, name: str, package_name: str, version: str, log: str) -> Path:
    """The install log of a previously installed package; an uninstall replays it backwards."""
    backup = folder / name / f"{package_name}_v{version}"
    backup.mkdir(parents=True)
    (backup / "install.log").write_text(log, encoding="utf-8")
    return backup


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


# ------------------------------------------------------------------- RootFolder: the node holds it


@pytest.mark.asyncio
async def test_root_install_is_refused_while_one_other_server_on_the_same_node_runs(tmp_path, caplog):
    """The named target is down, a sibling on the same node runs: nothing may be written."""
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    _root_zip(root)
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    busy = _server("SRS-2", node, Status.RUNNING)
    service = _service(root, saved_games, {"SRS-1": target, "SRS-2": busy})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(target, Folder.RootFolder, "SRS", "1.5.2") is False

    assert not (installation / "Mods" / "x.lua").exists(), "the DCS installation was written"
    assert not (root / ".node-1").exists(), "the backup folder was created"
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "SRS-2" in warnings[0], warnings[0]
    assert "SRS-1" not in warnings[0], "the shut-down target must not be named as a blocker"


@pytest.mark.asyncio
async def test_root_install_is_allowed_when_every_server_on_that_node_is_shut_down(tmp_path):
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    other = _server("SRS-2", node, Status.SHUTDOWN)
    service = _service(root, saved_games, {"SRS-1": target, "SRS-2": other})
    service.apool = Pool(Cursor())
    _root_zip(root, version="1.5.2")

    assert await service.install_package(target, Folder.RootFolder, "SRS", "1.5.2") is True

    assert (installation / "Mods" / "x.lua").exists()
    assert (root / ".node-1" / "SRS_v1.5.2" / "install.log").exists()


@pytest.mark.asyncio
async def test_root_install_is_allowed_when_an_unregistered_server_on_that_node_has_no_process(tmp_path):
    """A sibling recorded ``UNREGISTERED`` with no DCS process behind it does not hold the installation."""
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    idle = _server("SRS-2", node, Status.UNREGISTERED, process=False)
    service = _service(root, saved_games, {"SRS-1": target, "SRS-2": idle})
    _root_zip(root, version="1.5.2")

    assert await service.install_package(target, Folder.RootFolder, "SRS", "1.5.2") is True

    assert (installation / "Mods" / "x.lua").exists()
    assert (root / ".node-1" / "SRS_v1.5.2" / "install.log").exists()


@pytest.mark.asyncio
async def test_root_install_is_refused_while_an_unregistered_server_on_that_node_still_has_a_process(tmp_path, caplog):
    """The sibling is not registered, but its DCS process is alive: the installation is held."""
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    zombie = _server("SRS-2", node, Status.UNREGISTERED, process=True)
    service = _service(root, saved_games, {"SRS-1": target, "SRS-2": zombie})
    _root_zip(root)

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(target, Folder.RootFolder, "SRS", "1.5.2") is False

    assert not (installation / "Mods" / "x.lua").exists()
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "SRS-2" in warnings[0], warnings[0]
    assert "SRS-1" not in warnings[0], "the shut-down target must not be named as a blocker"


@pytest.mark.asyncio
async def test_root_install_through_the_root_method_is_refused_as_well(tmp_path, caplog):
    """``install_root_package`` is reachable without a server object, so it gates on its own."""
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    _root_zip(root)
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    service = _service(root, saved_games, {"SRS-2": _server("SRS-2", node, Status.RUNNING)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_root_package(node, "SRS", "1.5.2") is False

    assert not (installation / "Mods" / "x.lua").exists()
    assert "SRS-2" in _warnings(caplog)[0]


@pytest.mark.asyncio
async def test_a_running_server_on_a_different_node_does_not_block_a_root_install(tmp_path):
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    _root_zip(root)
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    other_node = _Node("node-2", remote=True)
    service = _service(root, saved_games, {
        "SRS-1": _server("SRS-1", node, Status.SHUTDOWN),
        "SRS-9": _server("SRS-9", other_node, Status.RUNNING),
    })

    assert await service.install_root_package(node, "SRS", "1.5.2") is True

    assert (installation / "Mods" / "x.lua").exists()


@pytest.mark.asyncio
async def test_the_master_reads_the_remote_servers_of_the_target_node_from_its_own_registry(tmp_path):
    """A master filters its own registry by node name; a remote server counts, a local one does not."""
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    installation.mkdir()
    _root_zip(root)
    local_node = _Node("node-1", remote=False)
    local_node.installation = str(installation)
    remote_node = _Node("node-2", remote=True)
    remote_node.installation = str(installation)
    service = _service(root, saved_games, {
        "SRS-1": _server("SRS-1", local_node, Status.RUNNING),
        "SRS-9": _server("SRS-9", remote_node, Status.SHUTDOWN),
    })
    # the remote node executed this call: no proxy hop, the method body runs here
    run = ModManagerService.install_root_package.__wrapped__

    assert await run(service, remote_node, "SRS", "1.5.2") is True
    assert (root / ".node-2" / "SRS_v1.5.2" / "install.log").exists()

    service.bus.servers["SRS-9"].status = Status.RUNNING
    assert await run(service, remote_node, "SRS", "1.5.2") is False


# ------------------------------------------------------------ SavedGames: the single instance holds it


@pytest.mark.asyncio
async def test_instance_install_is_refused_while_its_own_server_runs(tmp_path, caplog):
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.RUNNING, home=str(home))
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is False

    assert not (home / "Mods" / "x.lua").exists()
    assert not (saved_games / ".SRS-1").exists()
    assert "SRS-1" in _warnings(caplog)[0]


@pytest.mark.asyncio
async def test_instance_install_is_allowed_when_its_own_server_is_shut_down(tmp_path):
    """A busy server on the same node is irrelevant for an instance-scoped package."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.SHUTDOWN, home=str(home))
    busy = _server("SRS-2", node, Status.RUNNING)
    service = _service(root, saved_games, {"SRS-1": server, "SRS-2": busy})

    assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is True

    assert (home / "Mods" / "x.lua").exists()
    assert (saved_games / ".SRS-1" / "SRS_v1.5.2" / "install.log").exists()


@pytest.mark.asyncio
async def test_instance_install_is_refused_while_an_unregistered_server_still_has_a_process(tmp_path, caplog):
    """No live registration, but a DCS process of that instance: the folder is held, nothing is written."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.UNREGISTERED, home=str(home), process=True)
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is False

    assert not (home / "Mods" / "x.lua").exists(), "the instance was written"
    assert not (saved_games / ".SRS-1").exists(), "the backup folder was created"
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "SRS-1" in warnings[0], warnings[0]


@pytest.mark.asyncio
async def test_instance_install_is_refused_when_the_process_probe_raises(tmp_path, caplog):
    """A failing probe is not evidence of an idle instance: the write is refused and the server is named."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.UNREGISTERED, home=str(home), probe_raises=True)
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is False

    assert not (home / "Mods" / "x.lua").exists(), "the instance was written"
    assert not (saved_games / ".SRS-1").exists(), "the backup folder was created"
    warnings = _warnings(caplog)
    assert len(warnings) == 2, warnings
    assert "could not be checked" in warnings[0] and "SRS-1" in warnings[0], warnings[0]
    assert "held by server(s) SRS-1" in warnings[1], warnings[1]


@pytest.mark.asyncio
async def test_instance_install_is_allowed_when_an_unregistered_server_has_no_process(tmp_path, caplog):
    """An unregistered record with no DCS process behind it does not hold the folder: the write proceeds."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.UNREGISTERED, home=str(home), process=False)
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is True

    assert (home / "Mods" / "x.lua").exists(), "the package was not written"
    assert (saved_games / ".SRS-1" / "SRS_v1.5.2" / "install.log").exists()
    assert [s for s, _ in service.apool._cursor.statements if "INSERT INTO mm_packages" in s], \
        "the package was not recorded"
    assert not _warnings(caplog), _warnings(caplog)


@pytest.mark.asyncio
async def test_instance_install_is_refused_while_the_process_lives_but_the_mission_is_stopped(tmp_path, caplog):
    """``STOPPED`` is a live process with no mission, so it still holds the folder."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    _root_zip(saved_games, package_name="SRS")
    server = _server("SRS-1", node, Status.STOPPED, home=str(home), process=True)
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.install_package(server, Folder.SavedGames, "SRS", "1.5.2") is False

    assert not (home / "Mods" / "x.lua").exists()
    assert "SRS-1" in _warnings(caplog)[0]


# ------------------------------------------------------------------------- the removal paths mirror it


@pytest.mark.asyncio
async def test_root_uninstall_is_refused_while_one_other_server_on_the_same_node_runs(tmp_path, caplog):
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _backup(root, ".node-1", "SRS", "1.5.2", "w Mods/x.lua\n")
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    busy = _server("SRS-2", node, Status.RUNNING)
    service = _service(root, saved_games, {"SRS-1": target, "SRS-2": busy})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.uninstall_package(target, Folder.RootFolder, "SRS", "1.5.2") is False

    assert (installation / "Mods" / "x.lua").exists(), "the game file was removed"
    assert (root / ".node-1" / "SRS_v1.5.2" / "install.log").exists()
    messages = _warnings(caplog)
    assert len(messages) == 1, f"the delegated gate logged more than once: {messages}"
    assert "SRS-2" in messages[0]


@pytest.mark.asyncio
async def test_root_uninstall_is_allowed_when_every_server_on_that_node_is_shut_down(tmp_path):
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _backup(root, ".node-1", "SRS", "1.5.2", "w Mods/x.lua\n")
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    service = _service(root, saved_games, {"SRS-1": target})

    assert await service.uninstall_package(target, Folder.RootFolder, "SRS", "1.5.2") is True

    assert not (installation / "Mods" / "x.lua").exists()
    assert not (root / ".node-1" / "SRS_v1.5.2").exists(), "the backup folder was kept"


@pytest.mark.asyncio
async def test_root_uninstall_through_the_root_method_is_refused_as_well(tmp_path, caplog):
    root, saved_games, installation = tmp_path / "root", tmp_path / "sg", tmp_path / "dcs"
    root.mkdir()
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _backup(root, ".node-1", "SRS", "1.5.2", "w Mods/x.lua\n")
    node = _Node("node-1", remote=False)
    node.installation = str(installation)
    service = _service(root, saved_games, {"SRS-2": _server("SRS-2", node, Status.PAUSED)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.uninstall_root_package(node, "SRS", "1.5.2") is False

    assert (installation / "Mods" / "x.lua").exists()
    assert "SRS-2" in _warnings(caplog)[0]


@pytest.mark.asyncio
async def test_instance_uninstall_is_refused_while_its_own_server_runs(tmp_path, caplog):
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    (home / "Mods").mkdir(parents=True)
    (home / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _backup(saved_games, ".SRS-1", "SRS", "1.5.2", "w Mods/x.lua\n")
    node = _Node("node-1", remote=False)
    server = _server("SRS-1", node, Status.LOADING, home=str(home))
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.uninstall_package(server, Folder.SavedGames, "SRS", "1.5.2") is False

    assert (home / "Mods" / "x.lua").exists()
    assert "SRS-1" in _warnings(caplog)[0]


@pytest.mark.asyncio
async def test_instance_uninstall_is_allowed_when_its_own_server_is_shut_down(tmp_path):
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    (home / "Mods").mkdir(parents=True)
    (home / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    _backup(saved_games, ".SRS-1", "SRS", "1.5.2", "w Mods/x.lua\n")
    node = _Node("node-1", remote=False)
    server = _server("SRS-1", node, Status.SHUTDOWN, home=str(home))
    service = _service(root, saved_games, {"SRS-1": server})

    assert await service.uninstall_package(server, Folder.SavedGames, "SRS", "1.5.2") is True

    assert not (home / "Mods" / "x.lua").exists()
    assert not (saved_games / ".SRS-1" / "SRS_v1.5.2").exists(), "the backup folder was kept"


@pytest.mark.asyncio
async def test_force_does_not_bypass_the_gate(tmp_path, caplog):
    """``force`` rebuilds a missing install log; it says nothing about server state."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    (home / "Mods").mkdir(parents=True)
    (home / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    node = _Node("node-1", remote=False)
    server = _server("SRS-1", node, Status.RUNNING, home=str(home))
    service = _service(root, saved_games, {"SRS-1": server})

    with caplog.at_level(logging.WARNING, logger=LOG):
        assert await service.uninstall_package(server, Folder.SavedGames, "SRS", "1.5.2",
                                               force=True) is False

    assert (home / "Mods" / "x.lua").exists()
    assert "SRS-1" in _warnings(caplog)[0]


# ------------------------------------------------------------------------------- the helper itself


def test_the_helper_names_the_blockers_of_either_folder(tmp_path):
    root, saved_games = tmp_path / "root", tmp_path / "sg"
    node = _Node("node-1", remote=False)
    other = _Node("node-2", remote=True)
    service = _service(root, saved_games, {
        "SRS-2": _server("SRS-2", node, Status.RUNNING),
        "SRS-1": _server("SRS-1", node, Status.LOADING),
        "SRS-0": _server("SRS-0", node, Status.SHUTDOWN),
        "SRS-9": _server("SRS-9", other, Status.RUNNING),
    })

    async def blocking(reference, folder):
        return await service._blocking_servers(reference, folder)

    assert asyncio.run(blocking(node, Folder.RootFolder)) == ["SRS-1", "SRS-2"]
    assert asyncio.run(blocking(other, Folder.RootFolder)) == ["SRS-9"]
    assert asyncio.run(blocking(service.bus.servers["SRS-0"], Folder.SavedGames)) == []
    assert asyncio.run(blocking(service.bus.servers["SRS-9"], Folder.SavedGames)) == ["SRS-9"]


def test_the_helper_answers_an_unregistered_server_by_its_process(tmp_path):
    """``UNREGISTERED`` is read from ``is_running()``; every other state is read from ``status``."""
    root, saved_games = tmp_path / "root", tmp_path / "sg"
    node = _Node("node-1", remote=False)
    service = _service(root, saved_games, {
        "SRS-1": _server("SRS-1", node, Status.UNREGISTERED, process=False),
        "SRS-2": _server("SRS-2", node, Status.UNREGISTERED, process=True),
        "SRS-3": _server("SRS-3", node, Status.STOPPED, process=False),
    })

    async def blocking(reference, folder):
        return await service._blocking_servers(reference, folder)

    assert asyncio.run(blocking(node, Folder.RootFolder)) == ["SRS-2", "SRS-3"]
    assert asyncio.run(blocking(service.bus.servers["SRS-1"], Folder.SavedGames)) == []
    assert asyncio.run(blocking(service.bus.servers["SRS-2"], Folder.SavedGames)) == ["SRS-2"]
