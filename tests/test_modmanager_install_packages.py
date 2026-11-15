"""Pins for the verdict of ``install_packages``: a closed gate is not a missing package.

The loop writes only while the target is down, so the only refusal left on the first-install path is the
gate. A refusal must read as a gate, with the blockers named, and must stop the server's remaining
packages the same way a target that is still up does. A ``False`` the gate did not cause stays
"not found" — the package really is absent then.
"""
from __future__ import annotations

import logging
import types

import pytest

try:
    from core.const import DEFAULT_TAG
    from core.data.const import Status
    from core.data.dataobject import DataObject
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)


LOG = "tests.modmanager.install_packages"


# --------------------------------------------------------------------------------------------- doubles


class _Node(DataObject):
    """A node double carrying a name; the proxy decorator serialises it by name."""

    def __init__(self, name: str, *, remote: bool):
        super().__init__(name=name, node=types.SimpleNamespace(pool=None, apool=None, config=None))
        self.is_remote = remote
        self.installation = ""


def _server(name: str, node: _Node, status: Status, *, home: str = "") -> types.SimpleNamespace:
    """A server double: the loop reads ``status``/``maintenance``, the gate reads ``node``."""
    return types.SimpleNamespace(
        name=name,
        status=status,
        node=node,
        is_remote=node.is_remote,
        maintenance=False,
        instance=types.SimpleNamespace(name=name, home=home),
    )


class Bus:
    """The process registry plus a recorder for the RPCs the proxy decorator would send."""

    def __init__(self, servers: dict[str, types.SimpleNamespace] | None = None):
        self.servers = servers if servers is not None else {}
        self.calls = []

    async def send_to_node_sync(self, message: dict, *, node: str | None = None, timeout=None):
        self.calls.append((message, node, timeout))
        return True


class Cursor:
    """Answers ``get_installed_package`` with "nothing installed" and records the statements."""

    def __init__(self):
        self.statements = []
        self.rowcount = 0

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        self.rowcount = 0
        return self

    async def fetchone(self):
        return None


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


class InstallSpy:
    """Stands in for ``install_package`` and records every call the loop makes."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.calls: list[tuple[str, Folder, str, str]] = []

    async def __call__(self, server, folder, package_name, version, repo=None):
        if isinstance(folder, str):
            folder = Folder(folder)
        self.calls.append((server.name, folder, package_name, version))
        return self.answer


def _service(root, saved_games, servers: dict,
             packages: list[dict] | None = None,
             per_instance: dict[tuple[str, str], list[dict]] | None = None) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry).

    ``packages`` configures every instance on the node, ``per_instance`` one instance only — the
    package list of a single configured server, which is what a node-pinned config file carries.
    """
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger(LOG)
    service.node = _Node("node-local", remote=False)
    service.bus = Bus(servers)
    default: dict = {"RootFolder": str(root), "SavedGames": str(saved_games)}
    if packages is not None:
        default["packages"] = packages
    service.locals = {DEFAULT_TAG: default}
    for (node_name, instance_name), instance_packages in (per_instance or {}).items():
        service.locals.setdefault(node_name, {}).setdefault(instance_name, {})["packages"] = instance_packages
    service._config = {}
    service.apool = Pool(Cursor())
    service.temp_packages = []
    return service


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


# ------------------------------------------------------------------- the gate decides, not the lookup


@pytest.mark.asyncio
async def test_a_closed_root_gate_stops_the_loop_instead_of_reporting_a_missing_package(tmp_path, caplog):
    """The target is down, a sibling on its node runs: the present package must not read as gone."""
    root, saved_games = tmp_path / "root", tmp_path / "sg"
    root.mkdir()
    saved_games.mkdir()
    node = _Node("node-1", remote=False)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    busy = _server("SRS-2", node, Status.RUNNING)
    service = _service(
        root, saved_games, {"SRS-1": target, "SRS-2": busy},
        per_instance={("node-1", "SRS-1"): [
            {"name": "SRS", "source": "RootFolder", "version": "1.5.2"},
            {"name": "UH-60", "source": "RootFolder", "version": "2.0"},
        ]})
    spy = InstallSpy()
    service.install_package = spy

    with caplog.at_level(logging.WARNING, logger=LOG):
        await service.install_packages()

    assert spy.calls == [], "the loop called into a write the gate refuses"
    warnings = _warnings(caplog)
    assert len(warnings) == 1, f"the refusal was reported more than once: {warnings}"
    assert "held by server(s) SRS-2." in warnings[0], warnings[0]
    assert "not found" not in warnings[0], warnings[0]
    assert "UH-60" not in warnings[0], "the loop did not stop at the refused server"


@pytest.mark.asyncio
async def test_the_loop_reports_a_missing_package_while_the_gate_is_open(tmp_path, caplog):
    """Every server on the node is down, so the ``False`` comes from the lookup itself."""
    root, saved_games = tmp_path / "root", tmp_path / "sg"
    root.mkdir()
    saved_games.mkdir()
    node = _Node("node-1", remote=False)
    target = _server("SRS-1", node, Status.SHUTDOWN)
    service = _service(root, saved_games, {"SRS-1": target},
                       packages=[{"name": "SRS", "source": "RootFolder", "version": "1.5.2"}])
    spy = InstallSpy(answer=False)
    service.install_package = spy

    with caplog.at_level(logging.WARNING, logger=LOG):
        await service.install_packages()

    assert spy.calls == [("SRS-1", Folder.RootFolder, "SRS", "1.5.2")]
    assert _warnings(caplog) == ["- Package SRS_v1.5.2 not found!"]


@pytest.mark.asyncio
async def test_an_up_target_still_stops_the_loop_with_the_shutdown_line(tmp_path, caplog):
    """The status check runs first: a target that is up is never reported as a gate refusal."""
    root, saved_games = tmp_path / "root", tmp_path / "sg"
    root.mkdir()
    saved_games.mkdir()
    node = _Node("node-1", remote=False)
    target = _server("SRS-1", node, Status.RUNNING)
    service = _service(root, saved_games, {"SRS-1": target},
                       packages=[{"name": "SRS", "source": "RootFolder", "version": "1.5.2"}])
    spy = InstallSpy()
    service.install_package = spy

    with caplog.at_level(logging.WARNING, logger=LOG):
        await service.install_packages()

    assert spy.calls == []
    assert _warnings(caplog) == [
        "  - Server SRS-1 needs to be shutdown to auto-install package SRS"
    ]


@pytest.mark.asyncio
async def test_the_savedgames_gate_answers_for_the_instance_not_for_the_node(tmp_path, caplog):
    """A sibling on the node runs, the target itself is down: the instance gate is open."""
    root, saved_games, home = tmp_path / "root", tmp_path / "sg", tmp_path / "home"
    root.mkdir()
    saved_games.mkdir()
    home.mkdir()
    node = _Node("node-1", remote=False)
    target = _server("SRS-1", node, Status.SHUTDOWN, home=str(home))
    busy = _server("SRS-2", node, Status.RUNNING)
    service = _service(
        root, saved_games, {"SRS-1": target, "SRS-2": busy},
        per_instance={("node-1", "SRS-1"): [
            {"name": "SRS", "source": "SavedGames", "version": "1.5.2"},
        ]})
    spy = InstallSpy()
    service.install_package = spy

    with caplog.at_level(logging.WARNING, logger=LOG):
        await service.install_packages()

    assert spy.calls == [("SRS-1", Folder.SavedGames, "SRS", "1.5.2")]
    assert _warnings(caplog) == []
