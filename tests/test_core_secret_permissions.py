"""The secret store must not be readable (or enterable) by anyone but its owner on POSIX.

``config/.secret/`` holds the bot token, the database passwords and the console's OAuth client
secret as pickles. A directory created under the usual umask is 0775 and each pickle is 0664, so any
local account can read every credential the bot has. The store is 0700 and each file 0600, and a
directory/file an older build left wide is narrowed rather than left as it is.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from core.utils.os import create_secret_dir, set_password

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_secret_dir_is_owner_only(tmp_path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()

    create_secret_dir(str(config_dir))

    assert _mode(config_dir / ".secret") == 0o700


@posix_only
def test_secret_file_is_owner_only(tmp_path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()

    set_password("probe_key", "probe_value", str(config_dir))

    assert _mode(config_dir / ".secret" / "probe_key.pkl") == 0o600


@posix_only
def test_a_wide_secret_dir_and_file_are_narrowed(tmp_path):
    config_dir = tmp_path / "cfg"
    store = config_dir / ".secret"
    store.mkdir(parents=True)
    os.chmod(store, 0o777)
    stale = store / "probe_key.pkl"
    stale.write_bytes(b"stale")
    os.chmod(stale, 0o666)

    set_password("probe_key", "probe_value", str(config_dir))

    assert _mode(store) == 0o700
    assert _mode(stale) == 0o600
