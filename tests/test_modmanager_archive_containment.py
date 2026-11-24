"""Pins for archive content and ``install.log`` content reaching a filesystem join.

An archive entry name and an ``install.log`` line are content, not a caller-supplied identity: both are
joined onto the installation and onto the package folder. A name that resolves outside those trees is
refused as a whole archive at install time, and skipped per entry at uninstall time, so a malformed
archive cannot write outside the installation and a corrupt log cannot remove or overwrite outside it.
An ordinary archive still installs and uninstalls with the same files and the same log shape.
"""
from __future__ import annotations

import logging
import os
import sys
import zipfile
from pathlib import Path

import pytest

try:
    from core.const import DEFAULT_TAG
    from services.modmanager.service import Folder, ModManagerService, _resolved_below
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from test_modmanager_node_aware import Bus, Cursor, Pool, _Node  # noqa: E402

LOG = "tests.modmanager.archive_containment"


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
    (installation / "sentinel.txt").write_text("keep me", encoding="utf-8")
    return installation


def _node(installation: Path) -> _Node:
    node = _Node("node-local", remote=False)
    node.installation = str(installation)
    return node


def _package_root(tmp_path: Path) -> Path:
    root = tmp_path / "RootFolder"
    root.mkdir()
    return root


def _zip(root: Path, entries: dict[str, str]) -> Path:
    filename = root / "Test_v1.0.zip"
    with zipfile.ZipFile(filename, "w") as zfile:
        for name, payload in entries.items():
            zfile.writestr(name, payload)
    return filename


# ------------------------------------------------- containment helper reads both separator spellings


@pytest.mark.parametrize("base,candidate,expected", [
    ("/dcs", "Mods/x.lua", True),
    ("/dcs", "Mods/../x.lua", True),
    ("/dcs/", "Mods/x.lua", True),
    ("/dcs", "./x.lua", True),
    ("/dcs", "../evil.txt", False),
    ("/dcs", "Mods/../../evil.txt", False),
    ("/dcs", "..\\evil.txt", False),
    ("/dcs", "Mods\\x.lua", True),
    ("/dcs", "..", False),
    ("/dcs", ".", False),
    ("/dcs", "/etc/passwd", False),
    ("/dcs", "C:\\Windows\\evil", False),
    ("/dcs", "C:evil", False),
    ("/dcs", "\\\\server\\share\\evil", False),
    ("/dcs", "../dcs-evil/x", False),
    ("/dcs", "", False),
    ("/dcs", "   ", False),
    ("/dcs", "a\x00b", False),
    ("/dcs", None, False),
    ("C:\\DCS", "Mods\\x.lua", True),
    ("C:\\DCS", "Mods/x.lua", True),
    ("C:\\DCS", "..\\evil.txt", False),
    ("C:\\DCS", "D:\\evil.txt", False),
    ("C:\\DCS", "C:\\DCS\\x.lua", False),
    ("C:\\DCS", "D:evil", False),
    ("C:\\DCS\\", "Mods\\x.lua", True),
    ("", "x", False),
])
def test_the_containment_helper_reads_every_separator_spelling(base, candidate, expected):
    """A Windows separator, a drive letter and a rooted path are outside on either platform."""
    assert (_resolved_below(base, candidate) is not None) is expected


# ------------------------------------------------ a malformed archive writes nothing, anywhere


@pytest.mark.parametrize("escaping", ["../evil.txt", "..\\evil.txt", "Mods/../../evil.txt"])
@pytest.mark.asyncio
async def test_an_escaping_entry_refuses_the_whole_install(tmp_path, caplog, escaping):
    """One entry outside the installation refuses the archive; no file lands outside, none inside."""
    root = _package_root(tmp_path)
    filename = _zip(root, {"Mods/x.lua": "return", escaping: "pwned"})
    installation = _installation(tmp_path)
    cursor = Cursor()
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)}, cursor)

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(filename))

    assert installed is False, "an archive with an escaping entry is refused"
    assert not (tmp_path / "evil.txt").exists(), "the escaping entry wrote outside the installation"
    assert not (installation / "evil.txt").exists()
    assert not (installation / "Mods" / "x.lua").exists(), "the install wrote the rest of the archive"
    assert (installation / "sentinel.txt").read_text(encoding="utf-8") == "keep me"
    assert not (root / ".node-local" / "Test_v1.0").exists(), "the refused install left a package folder"
    assert not cursor.statements, "a refused install is not recorded as installed"
    messages = _warnings(caplog)
    assert messages, "the refusal is reported, not silent"
    # the entry name is quoted in the message in whichever spelling the platform's reader hands
    # it over: compared with the separators folded, so the sentence is checked, not the slash
    reported = escaping.replace("\\", "/")
    assert any(reported in message.replace("\\\\", "/") for message in messages), messages


@pytest.mark.asyncio
async def test_an_absolute_entry_refuses_the_whole_install(tmp_path, caplog):
    """An absolute entry name cannot be joined onto the target at all."""
    root = _package_root(tmp_path)
    outside = tmp_path / "outside.txt"
    filename = _zip(root, {str(outside): "pwned"})
    installation = _installation(tmp_path)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(filename))

    assert installed is False
    assert not outside.exists()
    assert _warnings(caplog)


@pytest.mark.asyncio
async def test_a_refused_install_keeps_a_package_folder_it_did_not_create(tmp_path, caplog):
    """A pre-existing backup folder is left alone: only the folder this call created is removed."""
    root = _package_root(tmp_path)
    filename = _zip(root, {"../evil.txt": "pwned"})
    installation = _installation(tmp_path)
    backup = root / ".node-local" / "Test_v1.0"
    backup.mkdir(parents=True)
    (backup / "install.log").write_text("w sentinel.txt\n", encoding="utf-8")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(filename))

    assert installed is False
    assert (backup / "install.log").exists()
    assert (installation / "sentinel.txt").exists()


# -------------------------------------------------- a corrupt log cannot act outside its target


def _backup(root: Path, log: str) -> Path:
    backup = root / ".node-local" / "Test_v1.0"
    backup.mkdir(parents=True)
    (backup / "install.log").write_text(log, encoding="utf-8")
    return backup


@pytest.mark.asyncio
async def test_a_log_line_cannot_remove_outside_the_target(tmp_path, caplog):
    """The escaping line is skipped with a warning, the rest of the log is still applied."""
    root = _package_root(tmp_path)
    installation = _installation(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("survives", encoding="utf-8")
    installed_file = installation / "Mods" / "x.lua"
    installed_file.write_text("return", encoding="utf-8")
    backup = _backup(root, "w ../outside.txt\nw Mods/x.lua\n")
    (backup / "payload.txt").write_text("backup", encoding="utf-8")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(backup))

    assert removed is True, "a skipped entry does not fail the uninstall"
    assert outside.read_text(encoding="utf-8") == "survives", "the log removed a file outside the target"
    assert not installed_file.exists(), "the rest of the log was applied"
    assert not backup.exists(), "the package folder is still cleaned up"
    messages = _warnings(caplog)
    assert any("../outside.txt" in message for message in messages), messages
    assert any("1 install.log entry" in message for message in messages), messages


@pytest.mark.asyncio
async def test_a_log_copy_cannot_read_outside_the_package_folder(tmp_path, caplog):
    """An ``x`` line naming a source outside the package folder is skipped, so nothing is overwritten."""
    root = _package_root(tmp_path)
    installation = _installation(tmp_path)
    backup = _backup(root, "x ../payload.txt\n")
    # the source the unguarded join would read, one level above the package folder
    (root / ".node-local" / "payload.txt").write_text("outside payload", encoding="utf-8")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(backup))

    assert removed is True
    assert not (tmp_path / "payload.txt").exists(), "a file was written outside the installation"
    assert (installation / "sentinel.txt").read_text(encoding="utf-8") == "keep me"
    messages = _warnings(caplog)
    assert any("../payload.txt" in message for message in messages), messages


@pytest.mark.asyncio
async def test_a_log_target_outside_the_target_is_skipped_but_the_copy_inside_still_runs(tmp_path, caplog):
    """Two lines: one escapes, one does not; the second is applied and the escape is counted."""
    root = _package_root(tmp_path)
    installation = _installation(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    inside = installation / "restored.txt"
    inside.write_text("modified", encoding="utf-8")
    backup = _backup(root, "x ../outside.txt\nx restored.txt\n")
    (backup / "restored.txt").write_text("original", encoding="utf-8")
    (root / ".node-local" / "outside.txt").write_text("backup payload", encoding="utf-8")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(backup))

    assert removed is True
    assert outside.read_text(encoding="utf-8") == "keep me"
    assert inside.read_text(encoding="utf-8") == "original", "the in-target copy did not run"
    assert any("1 install.log entry" in message for message in _warnings(caplog))


# ----------------------------------------------------------- an ordinary archive is unchanged


@pytest.mark.asyncio
async def test_an_ordinary_archive_round_trips_unchanged(tmp_path):
    """Nested folders and an overridden file install and uninstall with the same files and log."""
    root = _package_root(tmp_path)
    filename = _zip(root, {
        "Mods/Mod/entry.lua": "return",
        "Scripts/hook.lua": "hook",
        "sentinel.txt": "replaced",
    })
    installation = _installation(tmp_path)
    backup = root / ".node-local" / "Test_v1.0"
    cursor = Cursor()
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)}, cursor)

    assert await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                    str(root), str(filename)) is True
    assert (installation / "Mods" / "Mod" / "entry.lua").read_text(encoding="utf-8") == "return"
    assert (installation / "Scripts" / "hook.lua").read_text(encoding="utf-8") == "hook"
    assert (installation / "sentinel.txt").read_text(encoding="utf-8") == "replaced"
    assert (backup / "sentinel.txt").read_text(encoding="utf-8") == "keep me", "the override was not backed up"
    assert (backup / "install.log").read_text(encoding="utf-8") == (
        "w Mods/Mod\n"
        "w Mods/Mod/entry.lua\n"
        "w Scripts\n"
        "w Scripts/hook.lua\n"
        "x sentinel.txt\n"
    ), "the install.log shape changed"
    statement, params = cursor.statements[-1]
    assert "INSERT INTO mm_packages" in statement
    assert params == ("node-local", "Test", "1.0", "RootFolder")

    assert await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                      str(backup)) is True
    assert not (installation / "Mods" / "Mod" / "entry.lua").exists()
    assert not (installation / "Scripts" / "hook.lua").exists()
    assert (installation / "sentinel.txt").read_text(encoding="utf-8") == "keep me", "the backup was not restored"
    assert not backup.exists()


@pytest.mark.asyncio
async def test_a_recreated_log_names_a_directory_entry_without_a_trailing_slash(tmp_path, monkeypatch):
    """An archive's own directory entries replay into the log as the tree walk writes them: no slash."""
    root = _package_root(tmp_path)
    filename = _zip(root, {"Mods/": "", "Mods/x.lua": "return",
                           "Scripts/": "", "Scripts/hook.lua": "hook"})
    installation = _installation(tmp_path)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    async def _unpacked(folder, package_name, version, repo=None):
        """The package's own name without the archive suffix, so the replay of the zip runs."""
        return str(filename)[:-len(".zip")]

    monkeypatch.setattr(ModManagerService, "_ensure_package", staticmethod(_unpacked))

    assert await service.recreate_install_log(_node(installation), Folder.RootFolder, "Test", "1.0") is True
    lines = (root / ".node-local" / "Test_v1.0" / "install.log").read_text(encoding="utf-8").splitlines()
    assert lines, "the archive replay wrote no log"
    assert [line for line in lines if line.endswith("/")] == [], f"a directory entry kept its slash: {lines}"
    assert "w Mods" in lines, lines
    assert "w Mods/x.lua" in lines, lines
    assert "w Scripts" in lines, lines
    assert "w Scripts/hook.lua" in lines, lines


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32",
                    reason="the case stages a FILE NAMED '..\\evil.txt', and Windows refuses it: "
                           "there a backslash separates, it is not a character")
async def test_a_directory_package_never_logs_a_name_outside_the_target(tmp_path, caplog):
    """A directory package's entry names are walked, so an escaping one is left out and never logged."""
    root = _package_root(tmp_path)
    package = root / "Test_v1.0"
    (package / "Mods").mkdir(parents=True)
    (package / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    # a backslash is an ordinary character in a POSIX file name, so this entry survives a real tree
    (package / "..\\evil.txt").write_text("pwned", encoding="utf-8")
    installation = _installation(tmp_path)
    backup = root / ".node-local" / "Test_v1.0"
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(package))

    assert installed is True
    assert (installation / "Mods" / "x.lua").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (installation / "..\\evil.txt").exists()
    log_text = (backup / "install.log").read_text(encoding="utf-8")
    assert "evil" not in log_text, log_text
    assert any("1 entry" in message for message in _warnings(caplog)), _warnings(caplog)
