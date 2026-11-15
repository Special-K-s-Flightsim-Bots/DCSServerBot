"""Pins for the package lookups: the name a caller supplies matches only the archive it names.

A package name and a version reach three ``glob`` patterns — the version enumeration of a folder and the
two archive lookups an install/recreate goes through. Those two strings are literal identity, not a
pattern: ``*``, ``?``, ``[`` and ``]`` are ordinary characters in a package name, and a name that carries
one has to find its own file. These pins drive the real service methods against a real temp folder, so a
widened pattern shows up as the wrong file (or no file) rather than as a string comparison.
"""
from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from test_modmanager_node_aware import Bus, Cursor, Pool, _Node  # noqa: E402

LOG = "tests.modmanager.glob_escape"


def _service(folders: dict, cursor: Cursor | None = None) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry)."""
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger(LOG)
    service.node = _Node("node-local", remote=False)
    service.bus = Bus()
    service.locals = {DEFAULT_TAG: dict(folders)}
    service._config = {}
    service.apool = Pool(cursor or Cursor())
    service.temp_packages = []
    return service


def _archive(folder: Path, name: str) -> Path:
    """Write a minimal package archive ``name`` into *folder* and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with zipfile.ZipFile(path, "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    return path


def _found(found: str | None) -> str | None:
    return Path(found).name if found else None


# ------------------------------------------------------------ the archive lookup names one file only


@pytest.mark.asyncio
async def test_a_bracketed_package_name_finds_its_own_archive(tmp_path):
    """``Foo[1]`` is one identity: the lookup returns the archive whose name carries the brackets."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo[1]_v1.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    found = await service._ensure_package(Folder.RootFolder, "Foo[1]", "1.0")

    assert _found(found) == "Foo[1]_v1.0.zip", "a bracketed name read as a character class finds another file"



#: A Windows filename may not carry ``?`` or ``*`` at all, so these cases cannot be STAGED there — the
#: guard is exercised through ``[`` and ``]``, which are legal on both platforms.
needs_illegal_names = pytest.mark.skipif(
    sys.platform == "win32", reason="a Windows filename cannot contain '?' or '*'")


@pytest.mark.asyncio
@needs_illegal_names
async def test_a_wildcard_package_name_does_not_match_a_sibling(tmp_path, caplog):
    """``Foo*`` names a package, so it finds its own archive and never a sibling's."""
    root = tmp_path / "RootFolder"
    _archive(root, "FooBar_v1.0.zip")
    _archive(root, "Foo*_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        sibling = await service._ensure_package(Folder.RootFolder, "Foo*", "1.0")

    assert sibling is None, "a wildcard in the name matched a sibling package's archive"
    assert _found(await service._ensure_package(Folder.RootFolder, "Foo*", "2.0")) == "Foo*_v2.0.zip"


@pytest.mark.asyncio
@needs_illegal_names
async def test_a_question_mark_package_name_does_not_match_a_sibling(tmp_path, caplog):
    """``?`` is one character of the name, so ``Foo?`` does not find ``FooX_v1.0.zip``."""
    root = tmp_path / "RootFolder"
    _archive(root, "FooX_v1.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        sibling = await service._ensure_package(Folder.RootFolder, "Foo?", "1.0")

    assert sibling is None, "a question mark in the name matched a sibling package's archive"
    _archive(root, "Foo?_v1.0.zip")
    assert _found(await service._ensure_package(Folder.RootFolder, "Foo?", "1.0")) == "Foo?_v1.0.zip"


@pytest.mark.asyncio
async def test_a_bracketed_version_finds_its_own_archive(tmp_path):
    """The version is literal too: ``2[1]`` does not read as a character class."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v21.zip")
    _archive(root, "Foo_v2[1].zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    found = await service._ensure_package(Folder.RootFolder, "Foo", "2[1]")

    assert _found(found) == "Foo_v2[1].zip", "a bracketed version matched a different archive"


@pytest.mark.asyncio
async def test_installing_a_bracketed_package_uses_its_own_archive(tmp_path):
    """The install path resolves through the same lookup and installs the package it was asked for."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo[1]_v1.0.zip")
    installation = tmp_path / "dcs"
    installation.mkdir()
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    assert await service.install_root_package(node, "Foo[1]", "1.0") is True
    assert (installation / "Mods" / "x.lua").exists()


# ------------------------------------------------------------- the version list names one package only


@pytest.mark.asyncio
async def test_the_version_list_of_a_bracketed_package_lists_only_its_own_files(tmp_path):
    """A sibling whose name starts with the character class' members is not a version of this package."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo[1]_v1.0.zip")
    _archive(root, "Foo11_v3.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    versions = await service.get_available_versions(_Node("node-local", remote=False), Folder.RootFolder, "Foo[1]")

    assert versions == ["1.0"], f"the version list read another package's files: {versions}"


@pytest.mark.asyncio
@needs_illegal_names
async def test_the_version_list_of_a_wildcard_name_lists_only_its_own_files(tmp_path):
    """``Foo*`` does not inherit the versions of ``FooBar``."""
    root = tmp_path / "RootFolder"
    _archive(root, "FooBar_v1.0.zip")
    _archive(root, "Foo*_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    versions = await service.get_available_versions(_Node("node-local", remote=False), Folder.RootFolder, "Foo*")

    assert versions == ["2.0"], f"the version list read a sibling package's files: {versions}"


# ---------------------------------------------------------------- an ordinary package is unchanged


@pytest.mark.asyncio
async def test_an_ordinary_package_resolves_exactly_as_before(tmp_path):
    """A plain name and a dotted version find their archive and their version list."""
    root = tmp_path / "RootFolder"
    _archive(root, "SRS_v2.1.2.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    assert _found(await service._ensure_package(Folder.RootFolder, "SRS", "2.1.2")) == "SRS_v2.1.2.zip"
    assert await service.get_available_versions(
        _Node("node-local", remote=False), Folder.RootFolder, "SRS") == ["2.1.2"]


def test_the_guard_still_accepts_these_names():
    """The fix is at the glob, not in what a package may be called."""
    from services.modmanager import service as service_module

    guard = getattr(service_module, "_leaf_name", None)
    assert guard is not None
    assert guard("Foo[1]") == "Foo[1]"
    assert guard("Foo*") == "Foo*"
    assert guard("Foo?") == "Foo?"
    assert guard("Foo]") == "Foo]"
    assert guard("2[1]") == "2[1]"
