"""Resource identity: one installation must get ONE name, from every cluster that can see it.

The spelling table is the point of this file. Two strings naming the same directory that do not collapse to one id
produce two resources, and nothing anywhere reports it — no error, no log line — the update simply runs twice, or not
at all, while the file locks say otherwise.

The Windows rules (case fold, drive letters, UNC) are pinned explicitly, because this suite runs on Linux, where
``os.path.normpath`` treats ``C:\\DCS`` as a relative *name* and ``normcase`` does nothing. ``resolve_links=False``
throughout the table for the same reason: those entries test *spelling*, not the filesystem, and POSIX ``realpath``
would glue the current directory onto a Windows path. Link resolution has its own test at the end.
"""
from __future__ import annotations

import pytest

from conftest import needs_symlinks, symlinks_available

from core.utils import resources

DCS = 'dcs_installation'
MACHINE = 'MACHINE-A'


def identity(path, resource_type: str = DCS, machine: str | None = MACHINE, *,
             case_insensitive: bool = True, resolve_links: bool = False):
    """The Windows spelling rules, asked for explicitly so they hold on whatever platform runs this file."""
    return resources.ResourceIdentity.of(path, resource_type, machine=machine,
                                         case_insensitive=case_insensitive, resolve_links=resolve_links)


# ------------------------------------------------------------------ the table: these MUST collapse to one id

@pytest.mark.parametrize("spelling, canonical", [
 # case fold, separator style and a trailing separator are one directory on Windows
    ('C:\\DCS World', 'c:/dcs world'),
    ('C:\\DCS World', 'c:/dcs world/'),
    ('C:\\DCS World', 'c:\\dcs world\\'),
    ('C:\\DCS World', 'C:/DCS World'),
 # collapsed '.' and '..' segments
    ('C:\\DCS World\\bin', 'C:\\DCS World\\.\\bin'),
    ('C:\\DCS World\\bin', 'C:\\DCS World\\other\\..\\bin'),
    ('c:/dcs world', 'C:\\DCS World\\..\\DCS World'),
 # doubled separators
    ('C:\\DCS World', 'C:\\\\DCS World'),
    ('c:/dcs world', 'C://DCS World//'),
 # a UNC is one spelling too
    ('\\\\NAS\\Share\\DCS', '//nas/share/dcs'),
    ('//nas/share/dcs', '\\\\nas\\share\\DCS\\\\'),
 # '..' at a root has nowhere to go and is dropped, never climbing above the share or the drive
    ('//nas/share', '//nas/share/..'),
    ('c:/', 'C:\\..'),
 #...but inside a share it is an ordinary step back up
    ('//nas/share', '//nas/share/dcs/..'),
])
def test_the_same_directory_has_one_id(spelling, canonical):
    assert identity(spelling).id == identity(canonical).id


def test_the_canonical_spelling_is_what_the_registry_stores():
    """The id is a hash, so the stored path is the only part a human can read — pin its shape."""
    assert resources.normalize_path('C:\\DCS World\\\\bin\\..\\bin',
                                    case_insensitive=True, resolve_links=False) == 'c:/dcs world/bin'
    assert resources.normalize_path('\\\\NAS\\Share\\DCS',
                                    case_insensitive=True, resolve_links=False) == '//nas/share/dcs'
    assert resources.normalize_path('C:\\', case_insensitive=True, resolve_links=False) == 'c:/'


@pytest.mark.parametrize("one, another", [
    ('C:\\DCS World', 'C:\\DCS World 2'),
    ('C:\\DCS', 'D:\\DCS'),                      # different roots
    ('C:\\DCS', 'C:\\DCS\\bin'),                  # a subdirectory is a different resource
    ('C:\\DCS\\..\\Other', 'C:\\DCS'),            # above the root is not the root
    ('//nas/share/dcs', '//nas/share2/dcs'),      # a share boundary is a boundary
    ('//nas/share/dcs', '//nas2/share/dcs'),      # and so is the host
])
def test_neighbouring_directories_stay_apart(one, another):
    assert identity(one).id != identity(another).id


def test_case_folding_is_a_deliberate_choice_not_an_accident():
    """POSIX filesystems are case-sensitive, so the fold must be askable — and default per platform."""
    assert identity('C:/DCS', case_insensitive=False).id != identity('c:/dcs', case_insensitive=False).id
    assert identity('C:/DCS', case_insensitive=True).id == identity('c:/dcs', case_insensitive=True).id


# ------------------------------------------------------------------ what the id is keyed on

def test_a_local_resource_is_keyed_on_its_machine():
    assert identity('C:\\DCS', machine='MACHINE-A').id != identity('C:\\DCS', machine='MACHINE-B').id


def test_a_network_resource_ignores_the_machine_entirely():
    """The share is shared by construction: keying on the machine would give every client its own copy of it."""
    one = identity('\\\\NAS\\Share\\DCS', machine='MACHINE-A')
    another = identity('\\\\NAS\\Share\\DCS', machine='MACHINE-B')
    assert one.id == another.id
    assert one.network and another.machine is None


def test_a_mapped_drive_identifies_as_the_share_behind_it(monkeypatch):
    """``Z:\\DCS`` looks local and is not: without this, two clients spelling one share never meet."""
    monkeypatch.setattr(resources, 'mapped_unc', lambda drive: '\\\\nas\\share' if drive.upper() == 'Z:' else None)
    mapped = identity('Z:\\DCS\\World')
    unmapped = identity('\\\\nas\\share\\DCS\\World')
    assert mapped.id == unmapped.id
    assert mapped.network
 # a drive that is not mapped stays a local path, keyed on its machine
    assert identity('Y:\\DCS').network is False
    assert identity('Y:\\DCS', machine='MACHINE-A').id != identity('Y:\\DCS', machine='MACHINE-B').id


def test_the_type_is_part_of_the_identity():
    """Two resource types can share one directory; the registry PK is the id, so they must not collide."""
    assert identity('C:\\DCS', DCS).id != identity('C:\\DCS', 'srs_installation').id


# ------------------------------------------------------------------ refusal over guessing

def test_a_missing_machine_id_refuses_rather_than_keying_on_the_path(monkeypatch):
    """A path-only id would differ from every other cluster's answer for the same installation — silently."""
    monkeypatch.setattr(resources, 'machine_guid', lambda: None)
    with pytest.raises(ValueError):
        resources.ResourceIdentity.of('C:\\DCS', DCS, case_insensitive=True, resolve_links=False)
 # a network path needs no machine id at all
    assert resources.ResourceIdentity.of('\\\\nas\\share\\dcs', DCS,
                                         case_insensitive=True, resolve_links=False).id


@pytest.mark.parametrize("path", ['', '   ', None])
def test_an_empty_path_refuses(path):
    with pytest.raises(ValueError):
        resources.ResourceIdentity.of(path, DCS, machine=MACHINE,
                                      case_insensitive=True, resolve_links=False)


# ------------------------------------------------------------------ the derived name itself

def test_the_id_is_a_sha256_hex_and_does_not_move():
    one = identity('C:\\DCS')
    assert len(one.id) == 64 and all(c in '0123456789abcdef' for c in one.id)
    assert one.id == identity('C:\\DCS').id


def test_a_variable_is_expanded_before_hashing(monkeypatch):
    """The node config expands %VAR% on Windows; the same call answers $VAR elsewhere."""
    monkeypatch.setenv('DCS_HOME', '/opt/dcs')
    assert resources.normalize_path('$DCS_HOME/DCS') == resources.normalize_path('/opt/dcs/DCS')


@needs_symlinks
def test_a_symlink_and_its_target_are_one_resource(tmp_path):
    """On Windows the same realpath call resolves junctions — the case a plain link check misses."""
    target = tmp_path / 'real'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    assert (resources.ResourceIdentity.of(str(link), DCS, machine=MACHINE).id
            == resources.ResourceIdentity.of(str(target), DCS, machine=MACHINE).id)
 # the same two paths are two resources when link resolution is off, which is what makes the call visible
    assert (resources.ResourceIdentity.of(str(link), DCS, machine=MACHINE, resolve_links=False).id
            != resources.ResourceIdentity.of(str(target), DCS, machine=MACHINE, resolve_links=False).id)
