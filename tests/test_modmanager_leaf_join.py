"""Pins for the package identity every mod write joins onto a package-folder path.

A package name and a version arrive from the RPC payload, and the service joins them onto
``<package folder>/.<target>/<package>_v<version>`` before it reads an ``install.log``, writes a file or
removes the tree recursively. A value that names a LOCATION instead of one leaf therefore reaches a
destructive join. These pins drive the real service methods with a fake node and a real temp folder, and
assert both the refusal and the absence of a file effect.
"""
from __future__ import annotations

import builtins
import logging
import os
import types
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from core.data.const import Status
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from test_modmanager_node_aware import Bus, Cursor, Pool, _Node  # noqa: E402

LOG = "tests.modmanager.leaf_join"


# --------------------------------------------------------------------------------------------- doubles


def _service(packages: dict, cursor: Cursor | None = None) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry)."""
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger(LOG)
    service.node = _Node("node-local", remote=False)
    service.bus = Bus()
    service.locals = {DEFAULT_TAG: dict(packages)}
    service._config = {}
    service.apool = Pool(cursor or Cursor())
    service.temp_packages = []
    return service


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


def _installation(tmp_path: Path) -> Path:
    installation = tmp_path / "dcs"
    (installation / "Mods").mkdir(parents=True)
    (installation / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    (installation / "sentinel.txt").write_text("keep me", encoding="utf-8")
    return installation


def _backup(directory: Path) -> Path:
    """A backup directory holding the log that names the sentinel, and one payload file of its own."""
    directory.mkdir(parents=True)
    (directory / "install.log").write_text("w sentinel.txt\n", encoding="utf-8")
    (directory / "payload.txt").write_text("backup payload", encoding="utf-8")
    return directory


def _joined(root: Path, name: str, version: str) -> Path:
    """The path the unguarded join builds for *name* and *version* inside the package folder."""
    return Path(os.path.normpath(os.path.join(root, ".node-local", name + "_v" + version)))


class OpenedFiles:
    """Records every path opened in this process, delegating to the real ``open``."""

    def __init__(self):
        self.paths: list[str] = []
        self._real = builtins.open

    def __call__(self, file, *args, **kwargs):
        self.paths.append(os.path.normpath(str(file)))
        return self._real(file, *args, **kwargs)

    def saw(self, path: Path) -> bool:
        return os.path.normpath(str(path)) in self.paths


#: a package name that names a LOCATION instead of one entry of the package folder
LOCATION_NAMES = ("../..", "../../evil", "a/b", "..", "/absolute")


def _location_layout(tmp_path: Path, name: str) -> tuple[Path, Path, str, Path]:
    """``(package folder, installation, package name, the backup directory the join points at)``."""
    root = tmp_path / "RootFolder"
    # the per-node folder a node holds once it has installed into this package folder
    (root / ".node-local").mkdir(parents=True)
    installation = _installation(tmp_path)
    package = str(tmp_path / "evil") if name == "/absolute" else name
    return root, installation, package, _backup(_joined(root, package, "1.0"))


# ------------------------------------------------------------------ the destructive join is refused


@pytest.mark.asyncio
@pytest.mark.parametrize("name", LOCATION_NAMES)
async def test_a_location_shaped_package_name_cannot_reach_the_removal_join(
        tmp_path, caplog, monkeypatch, name):
    """Nothing is removed and no ``install.log`` outside the package's own folder is read."""
    root, installation, package, backup = _location_layout(tmp_path, name)
    opened = OpenedFiles()
    monkeypatch.setattr(builtins, "open", opened)
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.uninstall_root_package(node, package, "1.0")

    assert removed is False, "a package name that names a location is refused, not acted on"
    assert (installation / "sentinel.txt").exists(), "the escaping path removed a file outside the folder"
    assert (backup / "payload.txt").exists(), "the escaping path was removed recursively"
    assert not opened.saw(backup / "install.log"), "an install.log outside the package folder was read"
    assert _warnings(caplog), "the refusal is reported, not silent"


@pytest.mark.asyncio
async def test_a_location_shaped_package_name_cannot_reach_the_savedgames_removal_join(
        tmp_path, caplog, monkeypatch):
    """The instance half joins the same way and is refused the same way."""
    saved_games = tmp_path / "SavedGames"
    (saved_games / ".SRS-1").mkdir(parents=True)
    home = tmp_path / "home"
    (home / "Mods").mkdir(parents=True)
    (home / "sentinel.txt").write_text("keep me", encoding="utf-8")
    backup = _backup(Path(os.path.normpath(os.path.join(saved_games, ".SRS-1", "../../evil_v1.0"))))
    opened = OpenedFiles()
    monkeypatch.setattr(builtins, "open", opened)
    server = types.SimpleNamespace(name="SRS-1", node=_Node("node-local", remote=False),
                                   is_remote=False, status=Status.SHUTDOWN,
                                   instance=types.SimpleNamespace(name="SRS-1", home=str(home)))
    service = _service({"RootFolder": str(tmp_path), "SavedGames": str(saved_games)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.uninstall_package(server, Folder.SavedGames, "../../evil", "1.0")

    assert removed is False
    assert (home / "sentinel.txt").exists()
    assert (backup / "payload.txt").exists()
    assert not opened.saw(backup / "install.log")
    assert _warnings(caplog)


@pytest.mark.asyncio
async def test_a_package_name_with_a_separator_cannot_reach_the_install_join(tmp_path, caplog):
    """A name that is a path is refused before the install builds its backup folder."""
    root = tmp_path / "RootFolder"
    (root / "a").mkdir(parents=True)
    with zipfile.ZipFile(root / "a" / "b_v1.0.zip", "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    installation = _installation(tmp_path)
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.install_root_package(node, "a/b", "1.0")

    assert installed is False
    assert not (root / ".node-local" / "a" / "b_v1.0").exists(), "a nested path was used as a backup folder"
    assert "return" == (installation / "Mods" / "x.lua").read_text(encoding="utf-8")
    assert _warnings(caplog)


# ---------------------------------------------------------------- a normal package is unchanged


@pytest.mark.asyncio
async def test_a_normal_package_still_installs_and_uninstalls_from_a_real_folder(tmp_path):
    root = tmp_path / "RootFolder"
    root.mkdir()
    with zipfile.ZipFile(root / "SRS_v1.5.2.zip", "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    installation = tmp_path / "dcs"
    installation.mkdir()
    cursor = Cursor()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)}, cursor)

    assert await service.install_root_package(node, "SRS", "1.5.2") is True
    assert (installation / "Mods" / "x.lua").exists()
    backup = root / ".node-local" / "SRS_v1.5.2"
    assert (backup / "install.log").exists()

    assert await service.uninstall_root_package(node, "SRS", "1.5.2") is True
    assert not (installation / "Mods" / "x.lua").exists()
    assert not backup.exists()
    statement, params = cursor.statements[-1]
    assert "DELETE FROM mm_packages" in statement
    assert params == ("node-local", "RootFolder", "SRS", "1.5.2")


# ---------------------------------------------------- the string the guard accepted is the one used


@pytest.mark.asyncio
async def test_the_value_the_guard_accepts_is_the_value_the_filesystem_sees(tmp_path, caplog):
    """A padded name is normalised once: the join, the glob and the backup folder all see one string."""
    root = tmp_path / "RootFolder"
    root.mkdir()
    with zipfile.ZipFile(root / "TestMod_v1.0.zip", "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    installation = tmp_path / "dcs"
    installation.mkdir()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.install_root_package(node, " TestMod ", " 1.0 ")

    assert installed is True
    assert (root / ".node-local" / "TestMod_v1.0" / "install.log").exists()
    assert sorted(os.listdir(root / ".node-local")) == ["TestMod_v1.0"]
    assert await service.uninstall_root_package(node, " TestMod ", " 1.0 ") is True
    assert not (root / ".node-local" / "TestMod_v1.0").exists()


def test_the_guard_answers_the_normalised_name_or_a_refusal():
    from services.modmanager import service as service_module

    guard = getattr(service_module, "_leaf_name", None)
    assert guard is not None, "the service exposes no guard that names the value it validated"
    assert guard(" TestMod ") == "TestMod"
    assert guard("SRS_Radio_Standalone") == "SRS_Radio_Standalone"
    assert guard("2.1.2") == "2.1.2"
    for refused in ("../..", "../../evil", "a/b", "a\\b", "/etc", "C:\\Windows", "", "   ", None,
                    "a\x00b", ".", ".."):
        assert guard(refused) is None, refused


def test_the_guard_refuses_a_windows_device_name_and_not_the_letters():
    """A leaf that names a device is refused; a name that merely carries the letters is not.

    On Windows a write to ``<folder>/CON`` targets a device, so the file that follows is never a file.
    Only the device names themselves are refused: a package whose name starts with those letters is
    ordinary content.
    """
    from services.modmanager import service as service_module

    guard = service_module._leaf_name
    for refused in ("CON", "con", "Con", "PRN", "AUX", "NUL", "nul", "COM1", "com9", "LPT1", "lpt9",
                    "CONIN$", "CONOUT$", "NUL.", "CON "):
        assert guard(refused) is None, refused
    for accepted in ("NULFIRE", "CONTRACT", "AUXILIARY", "Nulify", "COM10", "LPT0", "COM0", "SRS", "2.1.2"):
        assert guard(accepted) == accepted.strip(), accepted


@pytest.mark.asyncio
async def test_a_device_shaped_package_name_cannot_reach_the_install_join(tmp_path, caplog):
    """The refusal is the service's own: nothing is installed and the line says so."""
    root = tmp_path / "RootFolder"
    (root / "a").mkdir(parents=True)
    with zipfile.ZipFile(root / "a" / "NUL_v1.0.zip", "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    installation = _installation(tmp_path)
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.install_root_package(node, "NUL", "1.0")

    assert installed is False, "a package name that names a device is refused, not acted on"
    assert not (root / ".node-local").exists(), "a backup folder was built for a device name"
    assert _warnings(caplog), "the refusal is reported, not silent"
