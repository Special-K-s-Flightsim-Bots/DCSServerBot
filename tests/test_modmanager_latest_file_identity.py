"""Pins for the newest-file lookup: a package answers the newest file that IS that package.

The package folder is flat, so ``Foo_v1.0.zip`` and ``FooBar_v2.0.zip`` sit side by side, and so do
``SRS_v2.1.2.zip`` and ``SRS_Radio_Standalone_v3.0.zip``. The lookup reads every entry's ``parse_filename``
identity and compares that name with the package name, so a name that CONTAINS another package's name is
still its own package. These pins drive the real service methods against a real temp folder.
"""
from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from test_modmanager_node_aware import Bus, Cursor, Pool, _Node  # noqa: E402

LOG = "tests.modmanager.latest_file"


def _service(folders: dict) -> ModManagerService:
    """Build the service without its constructor (no config file, no runtime registry)."""
    service = ModManagerService.__new__(ModManagerService)
    service.name = "ModManager"
    service.log = logging.getLogger(LOG)
    service.node = _Node("node-local", remote=False)
    service.bus = Bus()
    service.locals = {DEFAULT_TAG: dict(folders)}
    service._config = {}
    service.apool = Pool(Cursor())
    service.temp_packages = []
    return service


def _archive(folder: Path, name: str) -> Path:
    """Write a minimal package archive ``name`` into *folder* and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with zipfile.ZipFile(path, "w") as zfile:
        zfile.writestr("Mods/x.lua", "return")
    return path


def _local() -> _Node:
    return _Node("node-local", remote=False)


# ------------------------------------------------------- a name that contains another package's name


@pytest.mark.asyncio
async def test_the_chat_update_column_reads_the_true_newest_file(tmp_path):
    """``/mods manage`` calls ``get_latest_version`` with a source and a name; it answers this package's file."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "FooBar_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    latest = await service.get_latest_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.0", f"the chat's Update column names another package's version: {latest!r}"


@pytest.mark.asyncio
async def test_a_package_does_not_inherit_a_longer_names_version(tmp_path):
    """``Foo`` answers the newest file of ``Foo``, never the newer ``FooBar`` next to it."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "FooBar_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    latest = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.0", f"the newest-file lookup read another package's file: {latest!r}"


@pytest.mark.asyncio
async def test_a_package_does_not_inherit_an_earlier_names_version(tmp_path):
    """A name that only ENDS with another package's name is that other package too."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "MyFoo_v9.9.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    latest = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.0", f"the newest-file lookup read another package's file: {latest!r}"


@pytest.mark.asyncio
async def test_a_prefix_name_and_the_longer_name_each_answer_their_own(tmp_path):
    """``SRS`` and ``SRS_Radio_Standalone`` are two packages in one folder, each with its own newest file."""
    root = tmp_path / "RootFolder"
    _archive(root, "SRS_v2.1.2.zip")
    _archive(root, "SRS_Radio_Standalone_v3.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    srs = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "SRS"})
    radio = await service._get_latest_file_version(
        {"source": Folder.RootFolder, "name": "SRS_Radio_Standalone"})

    assert srs == "2.1.2", f"the prefix package answered another package's version: {srs!r}"
    assert radio == "3.0", f"the longer name answered another package's version: {radio!r}"


# ------------------------------------------------------------------ the merged answer of one package


@pytest.mark.asyncio
async def test_the_merged_answer_carries_the_corrected_latest_and_downloaded(tmp_path):
    """``list_package_versions`` names the newest OWN file as ``latest``, and it is on disk."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "FooBar_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    answer = await ModManagerService.list_package_versions.__wrapped__(
        service, _local(), Folder.RootFolder, ["Foo", "FooBar"])

    assert answer["Foo"]["latest"] == "1.0", f"the row names another package's version: {answer['Foo']!r}"
    assert answer["Foo"]["downloaded"] is True, f"the row follows another package's file: {answer['Foo']!r}"
    assert answer["FooBar"]["latest"] == "2.0", f"the sibling row is wrong: {answer['FooBar']!r}"
    assert answer["FooBar"]["downloaded"] is True


# ---------------------------------------------------------------- the unchanged part of the answer


@pytest.mark.asyncio
async def test_the_newest_version_of_one_package_is_still_the_newest(tmp_path):
    """Several files of the same package still resolve to the highest version, ``v`` stripped."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "Foo_v1.10.zip")
    _archive(root, "FooBar_v9.9.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    latest = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.10", f"the version comparison changed: {latest!r}"


@pytest.mark.asyncio
async def test_a_package_with_no_file_answers_none(tmp_path):
    """No file of that package in the folder answers ``None``, the sibling's file does not count."""
    root = tmp_path / "RootFolder"
    _archive(root, "FooBar_v2.0.zip")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    assert await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"}) is None


@pytest.mark.asyncio
async def test_an_entry_that_does_not_parse_is_ignored(tmp_path, monkeypatch):
    """A non-package entry in the folder neither raises nor becomes the answer."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    _archive(root, "Foo_readme.zip")
    (root / "Foo.notes").write_text("x", encoding="utf-8")
    # read the folder newest-looking name first, so a non-package entry is read after a package file
    real_listdir = os.listdir
    monkeypatch.setattr("services.modmanager.service.os.listdir",
                        lambda p: sorted(real_listdir(p), reverse=True))
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    latest = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.0", f"an entry that does not parse became the answer: {latest!r}"


@pytest.mark.asyncio
async def test_the_folder_is_read_through_the_same_config_key(tmp_path, monkeypatch):
    """The lookup reads the same configured folder as before, an expanded environment variable included."""
    root = tmp_path / "RootFolder"
    _archive(root, "Foo_v1.0.zip")
    monkeypatch.setenv("MODMANAGER_PIN_FOLDER", str(root))
    service = _service({"RootFolder": "${MODMANAGER_PIN_FOLDER}", "SavedGames": str(tmp_path)})

    latest = await service._get_latest_file_version({"source": Folder.RootFolder, "name": "Foo"})

    assert latest == "1.0"
