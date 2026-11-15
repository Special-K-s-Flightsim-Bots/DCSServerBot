"""A symbolic link INSIDE a mod tree: the containment decision is made on the resolved pair.

An archive entry name and an ``install.log`` line can only be checked lexically as text — but the
join they are used for follows a link. A folder inside the installation (or inside the package folder)
that is a link pointing elsewhere therefore routes a write and a removal out of the tree a lexical
``commonpath`` pair calls "inside". The decision is :func:`core.utils.resolve_within`'s and nothing
else: the resolved form is what is judged, and the resolved form is what the writer receives.

Each pin is a RESULT: nothing lands outside and nothing is removed outside, while a path genuinely
inside both trees still installs and still uninstalls with the same files.
"""
from __future__ import annotations

import logging
import os

import pytest

from conftest import needs_symlinks, symlinks_available

try:
    from services.modmanager.service import Folder, ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from test_modmanager_archive_containment import (  # noqa: E402
    LOG, _backup, _installation, _node, _package_root, _service, _warnings, _zip,
)
from test_modmanager_node_aware import Cursor  # noqa: E402


def _linked(installation, outside, name="link"):
    """``installation/Mods/<name>`` as a link to *outside*: the lexical join stays "inside"."""
    link = installation / "Mods" / name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(outside, target_is_directory=True)
    return link


# ------------------------------------------------------------------- the decision, on the resolved pair


@needs_symlinks
def test_a_link_pointing_out_of_the_tree_is_not_a_place_inside_it(tmp_path):
    """The helper answers the RESOLVED path, and refuses a name the link routes out of the tree."""
    from services.modmanager.service import _resolved_below

    root = tmp_path / "dcs"
    (root / "Mods").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "Mods" / "link").symlink_to(outside, target_is_directory=True)

    assert _resolved_below(root, "Mods/link/evil.txt") is None
    assert _resolved_below(root, "Mods/plain.txt") == str(root / "Mods" / "plain.txt")


@needs_symlinks
def test_a_link_pointing_inside_the_tree_answers_the_resolved_path(tmp_path):
    """A link that stays below the tree is accepted, and the value is the file it really names."""
    from services.modmanager.service import _resolved_below

    root = tmp_path / "dcs"
    (root / "Mods").mkdir(parents=True)
    real = root / "Mods" / "real"
    real.mkdir()
    (root / "Mods" / "link").symlink_to(real, target_is_directory=True)

    answer = _resolved_below(root, "Mods/link/x.lua")
    assert answer is not None
    assert answer == os.path.realpath(real / "x.lua"), answer
    assert f"{os.sep}Mods{os.sep}link{os.sep}" not in answer, (
        "the writer would follow the link instead of the checked route")


# ------------------------------------------------------ an install cannot be routed out of the tree


@pytest.mark.asyncio
@needs_symlinks
async def test_an_install_through_a_symlinked_subfolder_writes_nothing_outside(tmp_path, caplog):
    """One entry below a link out of the installation refuses the archive; nothing lands anywhere."""
    root = _package_root(tmp_path)
    filename = _zip(root, {"Mods/x.lua": "return", "Mods/link/evil.txt": "pwned"})
    installation = _installation(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _linked(installation, outside)
    cursor = Cursor()
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)}, cursor)

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(filename))

    assert installed is False, "an entry routed through the link is refused"
    assert not (outside / "evil.txt").exists(), "the entry was written outside the installation"
    assert not (installation / "Mods" / "x.lua").exists(), "the rest of the archive was written"
    assert not (root / ".node-local" / "Test_v1.0").exists(), "a refused install left a package folder"
    assert not cursor.statements, "a refused install is not recorded as installed"
    assert _warnings(caplog), "the refusal is reported"


@pytest.mark.asyncio
@needs_symlinks
async def test_an_install_of_a_folder_package_through_a_symlinked_subfolder_writes_nothing_outside(
        tmp_path, caplog):
    """The same rule on the directory-package walk: the linked entry is left out, nothing outside."""
    root = _package_root(tmp_path)
    package = root / "Test_v1.0"
    (package / "Mods").mkdir(parents=True)
    (package / "Mods" / "x.lua").write_text("return", encoding="utf-8")
    (package / "Mods" / "link").mkdir()
    (package / "Mods" / "link" / "evil.txt").write_text("pwned", encoding="utf-8")
    installation = _installation(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _linked(installation, outside)
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        installed = await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(root), str(package))

    assert installed is True, "the installation itself is not refused, only the linked entry is"
    assert not (outside / "evil.txt").exists(), "the entry was written outside the installation"
    assert (installation / "Mods" / "x.lua").exists(), "the inside entry was not written"


# ---------------------------------------------------- a removal cannot be routed out of the tree


@pytest.mark.asyncio
@needs_symlinks
async def test_a_removal_through_a_symlinked_subfolder_removes_nothing_outside(tmp_path, caplog):
    """A log line below a link out of the installation is skipped; the file outside survives."""
    root = _package_root(tmp_path)
    installation = _installation(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "evil.txt"
    victim.write_text("survives", encoding="utf-8")
    _linked(installation, outside)
    backup = _backup(root, "w Mods/link/evil.txt\n")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(backup))

    assert removed is True, "a skipped entry does not fail the uninstall"
    assert victim.read_text(encoding="utf-8") == "survives", "the log removed a file outside the tree"
    assert any("Mods/link/evil.txt" in message for message in _warnings(caplog)), _warnings(caplog)


@pytest.mark.asyncio
@needs_symlinks
async def test_a_restore_through_a_symlinked_subfolder_writes_nothing_outside(tmp_path, caplog):
    """An ``x`` line below a link out of the installation overwrites nothing outside it."""
    root = _package_root(tmp_path)
    installation = _installation(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "evil.txt"
    victim.write_text("survives", encoding="utf-8")
    _linked(installation, outside)
    backup = _backup(root, "x Mods/link/evil.txt\n")
    (backup / "Mods").mkdir()
    (backup / "Mods" / "link").mkdir()
    (backup / "Mods" / "link" / "evil.txt").write_text("backup", encoding="utf-8")
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    with caplog.at_level(logging.WARNING, logger=LOG):
        removed = await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                             str(backup))

    assert removed is True
    assert victim.read_text(encoding="utf-8") == "survives", "the restore wrote outside the tree"
    assert _warnings(caplog)


# --------------------------------------------------- a path genuinely inside is still installed


@pytest.mark.asyncio
@needs_symlinks
async def test_a_link_that_stays_inside_the_tree_still_installs_and_uninstalls(tmp_path):
    """The positive control: nothing about a legitimately inside route changed."""
    root = _package_root(tmp_path)
    filename = _zip(root, {"Mods/real/entry.lua": "return"})
    installation = _installation(tmp_path)
    (installation / "Mods" / "real").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    # a link that points INTO the installation: the route is inside on the resolved pair
    (installation / "Mods" / "link").symlink_to(installation / "Mods" / "real", target_is_directory=True)
    backup = root / ".node-local" / "Test_v1.0"
    service = _service({"RootFolder": str(root), "SavedGames": str(tmp_path)})

    assert await service.do_install(_node(installation), Folder.RootFolder, "Test", "1.0",
                                    str(root), str(filename)) is True
    assert (installation / "Mods" / "real" / "entry.lua").read_text(encoding="utf-8") == "return"
    assert (backup / "install.log").read_text(encoding="utf-8") == (
        "w Mods/real\nw Mods/real/entry.lua\n")

    assert await service.do_uninstall(_node(installation), Folder.RootFolder, "Test", "1.0",
                                      str(backup)) is True
    assert not (installation / "Mods" / "real" / "entry.lua").exists()
