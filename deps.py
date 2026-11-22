"""Keep this node's virtual environment in line with its requirement files.

Every node has its own environment (``.dcssb-<node>``), while an update installs only into the
environment of the process that ran it. A node that restarts after an upgrade can therefore come up
on its previous environment: the code is new, the packages are not. This module is run at the start
of every boot, before any third-party import, and synchronises the environment when the requirement
files no longer match what it was built from.

``uv pip sync`` is skipped when the fingerprint matches, so an ordinary start costs one hash of two
small text files. The fingerprint lives inside the environment, which makes it per node by
construction and makes a failed synchronisation retry on the next start: it is written only after a
run that succeeded. Nothing here may import a third-party module - this is the code that has to work
while the environment is incomplete.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

#: Requirement files, in the order ``uv pip sync`` receives them. The second is optional and is the
#: documented way to add plugin dependencies.
REQUIREMENT_FILES: tuple[str, ...] = ('requirements.txt', 'requirements.local')

#: Name of the fingerprint file, stored in the environment's root directory (``sys.prefix``).
STAMP_NAME = 'requirements.sha256'


def requirement_files() -> list[str]:
    """The requirement files present in the installation directory, in sync order."""
    return [name for name in REQUIREMENT_FILES if os.path.isfile(name)]


def fingerprint() -> str:
    """A digest over the names and bytes of the requirement files.

    Names are hashed too, so swapping a file for one with the other's content still counts as a
    change. No requirement file yields the digest of nothing, which never matches a written stamp.
    """
    digest = hashlib.sha256()
    for name in requirement_files():
        digest.update(name.encode('utf-8'))
        digest.update(b'\0')
        with open(name, 'rb') as infile:
            for chunk in iter(lambda: infile.read(65536), b''):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest()


def stamp_path() -> str | None:
    """The fingerprint file of the environment this process runs in, or None if it is not one.

    A stamp only describes a virtual environment. For a bare interpreter there is nothing to
    describe, and recording one there would claim a system-wide installation matches this
    installation's requirement files.
    """
    if sys.prefix == sys.base_prefix:
        return None
    return os.path.join(sys.prefix, STAMP_NAME)


def needs_sync() -> bool:
    """Whether the environment must be synchronised before it can serve this code."""
    path = stamp_path()
    if path is None or not requirement_files():
        return False
    try:
        with open(path, 'r', encoding='utf-8') as infile:
            return infile.read().strip() != fingerprint()
    except OSError:
        return True


def write_stamp() -> None:
    """Record the current fingerprint. Only for a synchronisation that succeeded."""
    path = stamp_path()
    if path is None:
        return
    try:
        with open(path, 'w', encoding='utf-8') as outfile:
            outfile.write(fingerprint())
    except OSError as ex:
        print(f"  => Could not record the environment state in {path}: {ex}", flush=True)


def sync(uv: str | None = None) -> int:
    """Synchronise this interpreter's environment with the requirement files.

    Returns the exit code of ``uv``, or ``-1`` when uv is not available. ``uv`` comes from PATH by
    default, which is where both launchers put it before calling this module.
    """
    uv = uv or 'uv'
    if not shutil.which(uv):
        print("uv was not found in your PATH - DCSServerBot needs it to install its dependencies.",
              flush=True)
        print("Install it from https://docs.astral.sh/uv/ and start this node again.", flush=True)
        return -1
    cmd = [uv, 'pip', 'sync', '--python', sys.executable, *requirement_files()]
    return subprocess.run(cmd).returncode


def ensure(uv: str | None = None) -> int:
    """Synchronise the environment when it is out of date; a no-op when it is not.

    Returns 0 when the environment matches the requirement files (including "already matched"), and
    a non-zero exit code when it could not be brought up to date.
    """
    if not needs_sync():
        return 0
    print("- The requirement files changed since this environment was built. Synchronising ...",
          flush=True)
    rc = sync(uv)
    if rc:
        print("  => The environment could not be synchronised with the requirement files.", flush=True)
        return rc
    write_stamp()
    return 0


if __name__ == '__main__':
    exit(ensure(sys.argv[1] if len(sys.argv) > 1 else None))