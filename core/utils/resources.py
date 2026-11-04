"""
Resource identity for a federation.

A *resource* is anything several clusters can share on one machine — a DCS installation today, an SRS or Tacview
installation later. Coordination needs **one stable name per physical resource**, derived independently and
identically by every cluster that can see it, because nothing can be stored next to the resource itself: the DCS
installer wipes foreign files inside its installation, so a marker there does not survive an update.

So the name is derived, never stored:

    resource_id = sha256(resource_type + '|' + machine + '|' + normalized_path)

Everything rests on the normalization being *identical* everywhere. Two spellings that do not collapse to one
string produce two ids, and coordination then does **nothing at all** — no error, no log line, the update simply
runs twice or not at all. That table is pinned in ``tests/test_resource_identity.py``.

A network path (a UNC, or a drive mapped to one) is shared *by construction* between the machines that mount it, so
it is keyed on the resolved UNC alone: the machine identity must not split one NAS installation into one resource
per client.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys

from dataclasses import dataclass

__all__ = [
    "ResourceIdentity",
    "machine_guid",
    "normalize_path",
    "is_network_path"
]

logger = logging.getLogger(__name__)

#: Read without elevation (only *writing* needs it), stable across DCS updates by construction, and nothing the DCS
#: installer can delete because nothing is written.
_WINDOWS_MACHINE_KEY = r"SOFTWARE\Microsoft\Cryptography"

#: ``X:`` / ``x:\`` at the start of a path — the only place a drive letter is meaningful.
_DRIVE = re.compile(r'^([a-zA-Z]):')


def machine_guid() -> str | None:
    """This machine's stable identifier, or ``None`` when the platform cannot provide one.

    Windows reads ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``. Anywhere else it falls back to
    ``/etc/machine-id`` so a development node behaves the same way.

    ``None`` must be treated as a refusal (see :meth:`ResourceIdentity.of`), never as "key on the path instead": a
    path-only id differs from the one every other cluster computes for the same installation, which is exactly the
    silent mismatch this module exists to prevent.
    """
    if sys.platform == 'win32':
        # noinspection PyUnresolvedReferences
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WINDOWS_MACHINE_KEY, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, 'MachineGuid')[0]
        except OSError:
            logger.warning("- MachineGuid is not readable from the registry.", exc_info=True)
            return None
    try:
        with open('/etc/machine-id', mode='r', encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def mapped_unc(drive: str) -> str | None:
    """The UNC behind a mapped drive letter, or ``None`` when it is not a mapped drive (Windows only).

    A mapped network drive *is* a network resource, and the path itself does not say so — ``Z:\\DCS`` looks local.
    Without this, two machines mounting the same share under different letters would register two resources; with
    it, both resolve to the share and agree.
    """
    if sys.platform != 'win32' or not drive:
        return None
    # noinspection PyUnresolvedReferences
    import ctypes
    from ctypes import wintypes
    buffer = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(buffer))
    # noinspection PyUnresolvedReferences
    result = ctypes.windll.mpr.WNetGetConnectionW(drive, buffer, ctypes.byref(size))
    return buffer.value if result == 0 else None


def _split_root(path: str) -> tuple[str, list[str]]:
    """Split a Windows- or POSIX-shaped path into its root and its meaningful segments.

    Deliberately not ``os.path``: those functions parse per platform, so ``C:\\DCS`` handed to ``os.path.normpath``
    on Linux is treated as a relative *name* and gets the current directory glued in front of it. Identity has to
    come out the same no matter which platform computes it.
    """
    path = path.replace('\\', '/')
    if path.startswith('//'):
        # UNC: host and share are the root and a '..' may not climb out of them.
        parts = [p for p in path[2:].split('/') if p]
        return '//' + '/'.join(parts[:2]), parts[2:]
    drive = _DRIVE.match(path)
    if drive:
        return drive.group(1).lower() + ':', [p for p in path[2:].split('/') if p and p != '.']
    if path.startswith('/'):
        return '/', [p for p in path[1:].split('/') if p and p != '.']
    return '', [p for p in path.split('/') if p and p != '.']


def normalize_path(path: str, *, case_insensitive: bool | None = None,
                   resolve_links: bool = True) -> str:
    """The one spelling of *path* that every cluster must agree on.

    Applies, in order: variable expansion, junction/symlink resolution, ``.``/``..`` collapsing, one separator
    style, an optional case fold, and no trailing separator.

    :param case_insensitive: fold case (Windows semantics). Defaults to the current platform, and is a parameter so
        the Windows rules can be pinned by a test running elsewhere.
    :param resolve_links: resolve symlinks and junctions. Off is for callers that hold a path which does not exist
        yet, and for tests of the *spelling* rules, which are not about the filesystem.
    """
    path = os.path.expandvars(path)
    if resolve_links and path:
        # realpath also resolves junctions on Windows, which is the case a plain link check misses.
        with_expanded = os.path.realpath(path)
        if with_expanded:
            path = with_expanded
    root, segments = _split_root(path)
    collapsed: list[str] = []
    for segment in segments:
        if segment == '..':
            if collapsed:
                collapsed.pop()
            # a '..' above the root is dropped: nothing outside a resource's root can be part of its identity
        else:
            collapsed.append(segment)
    if collapsed:
        # a drive root is 'c:' and needs the separator back ('c:' + 'dcs' would read as one segment); '' and '/' and
        # '//host/share' all end at a separator already
        joiner = '' if not root or root.endswith('/') else '/'
        result = root + joiner + '/'.join(collapsed)
    else:
        # nothing but a root: a bare drive keeps its separator ('C:' means "somewhere on C:", 'C:/' the root itself),
        # while '' and '/' and '//host/share' are already complete
        result = root + '/' if root.endswith(':') else root
    if case_insensitive is None:
        case_insensitive = sys.platform == 'win32'
    return result.lower() if case_insensitive else result


def is_network_path(normalized_path: str) -> bool:
    """True when an already-normalized path names a network location (a UNC)."""
    return normalized_path.startswith('//')


@dataclass(frozen=True)
class ResourceIdentity:
    """One resource's derived name, plus what it was derived from — for the log line and the registry row."""

    id: str
    resource_type: str
    path: str
    machine: str | None
    network: bool

    @classmethod
    def of(cls, path: str, resource_type: str, *, machine: str | None = None,
           case_insensitive: bool | None = None, resolve_links: bool = True) -> "ResourceIdentity":
        """Derive the identity of *path* as *resource_type*.

        :param machine: the machine id to key on. Looked up when omitted; a ``None`` lookup means the caller gets a
            :class:`ValueError` rather than a path-only id — see :func:`machine_guid`.
        :raises ValueError: when the path is empty, or a local resource has no machine identity to key on.
        """
        if not path or not str(path).strip():
            raise ValueError("A resource path must not be empty.")
        # A mapped drive is a network path wearing a local letter, and it has to be recognised *before* the path is
        # normalised: 'Z:/DCS' and '//nas/dcs' must produce the same id, or the same share registers once per client.
        drive = _DRIVE.match(str(path).replace('\\', '/'))
        if drive:
            unc = mapped_unc(drive.group(1) + ':')
            if unc:
                # WNetGetConnectionW answers with the *share* ('\\nas\share'), not the path inside it, so the
                # remainder of the original path has to be appended or 'Z:\DCS' would identify the whole share.
                path = unc + str(path)[drive.end():]
        normalized = normalize_path(str(path), case_insensitive=case_insensitive, resolve_links=resolve_links)
        network = is_network_path(normalized)
        if network:
            key = f"{resource_type}|{normalized}"
            identified_by = None
        else:
            identified_by = machine or machine_guid()
            if not identified_by:
                raise ValueError(
                    f"Cannot identify the resource at '{normalized}': this machine has no readable machine id. "
                    f"A path-only id would differ from the one other clusters derive for the same installation."
                )
            key = f"{resource_type}|{identified_by}|{normalized}"
        return cls(
            id=hashlib.sha256(key.encode('utf-8')).hexdigest(),
            resource_type=resource_type,
            path=normalized,
            machine=identified_by,
            network=network
        )
