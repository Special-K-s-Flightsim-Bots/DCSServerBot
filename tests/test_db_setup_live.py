"""Live PostgreSQL gate for ``core.utils.db_setup`` (Phase 0's DB DDL).

Creates a throwaway role AND database through ``provision_database`` against a real PostgreSQL,
then drops them. Skipped when no PostgreSQL is configured, so the automated runs stay honest:
a skip means the DDL was NOT exercised live here, not that it passed.

Point it at a throwaway server with ``DCSSB_TEST_PG_MASTER_URL`` (a FULL DSN for an account that
may CREATE ROLE / CREATE DATABASE), e.g.::

    DCSSB_TEST_PG_MASTER_URL=postgres://postgres:pw@127.0.0.1:5432/postgres pytest -k live

The DDL it runs is the installer's own; the role/database are named uniquely and removed again.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

psycopg = pytest.importorskip('psycopg')

from core.utils import db_setup  # noqa: E402
from core.utils.db_setup import DatabaseProvisionIO  # noqa: E402

MASTER_URL = os.environ.get('DCSSB_TEST_PG_MASTER_URL')

pytestmark = pytest.mark.skipif(
    not MASTER_URL,
    reason='no DCSSB_TEST_PG_MASTER_URL set -- live PostgreSQL DDL not exercised here')


class _IO(DatabaseProvisionIO):
    def __init__(self, master_user, master_db, master_password):
        self._master_user = master_user
        self._master_db = master_db
        self._master_password = master_password

    def master_credentials(self):
        return self._master_db, self._master_user

    def master_password(self, master_user):
        return self._master_password


def _split_master_url(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.hostname, parsed.port or 5432, parsed.username, (parsed.password or ''), \
        parsed.path.lstrip('/')


def test_live_provision_creates_role_and_database(tmp_path):
    host, port, master_user, master_password, master_db = _split_master_url(MASTER_URL)
    suffix = uuid.uuid4().hex[:8]
    user = f'dcssb_test_{suffix}'
    database = f'dcssb_test_{suffix}'
    config_dir = tmp_path / 'config'
    (config_dir / '.secret').mkdir(parents=True)

    io = _IO(master_user, master_db, master_password)
    result = db_setup.provision_database(host, port, user, database, str(config_dir), io)
    assert result is not None, 'provisioning failed against the configured PostgreSQL'
    assert result.created is True

    # the role can log in with the password the installer stored, and the database exists
    from core import utils
    bot_password = utils.get_password('database', str(config_dir))
    with psycopg.connect(db_setup.connection_url(user, bot_password, host, port, database)):
        pass

    # clean up the throwaway objects
    with psycopg.connect(MASTER_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
        conn.execute(f'DROP ROLE IF EXISTS "{user}"')
