"""``core.utils.db_setup`` runs the installer's PostgreSQL DDL and classifies the driver errors.

The statements and the retry/recovery sequence are pinned here with a fake connection; the SQL is
the installer's own. A real PostgreSQL is not required for these (and none is available in this
environment) -- see ``tests/test_db_setup_live.py`` for the live gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils import db_setup  # noqa: E402
from core.utils.db_setup import DatabaseProvisionIO, provision_database  # noqa: E402


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Records every statement and answers the version probe. Flags make a statement raise."""

    def __init__(self, *, version='16.2', user_exists=False, database_exists=False):
        self.version = version
        self.user_exists = user_exists
        self.database_exists = database_exists
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        text = str(query)
        self.statements.append(text)
        if 'server_version_num' in text:
            return _Cursor((self.version,))
        if 'CREATE USER' in text and self.user_exists:
            raise psycopg.Error('role "x" already exists')
        if 'CREATE DATABASE' in text and self.database_exists:
            raise psycopg.Error('database "x" already exists')
        return _Cursor((None,))

    def statements_matching(self, needle):
        return [s for s in self.statements if needle in s]


class FakeIO(DatabaseProvisionIO):
    def __init__(self, *, master=('postgres', 'postgres'), master_passwords=None,
                 user_passwords=None):
        self._master = master
        self._master_passwords = list(master_passwords or ['mpw'])
        self._user_passwords = list(user_passwords or [])
        self.calls = []

    def master_credentials(self):
        self.calls.append('master_credentials')
        return self._master

    def master_password(self, master_user):
        self.calls.append('master_password')
        return self._master_passwords.pop(0)

    def bot_user_password(self, user):
        self.calls.append('bot_user_password')
        return self._user_passwords.pop(0)

    def on_outdated_version(self):
        self.calls.append('on_outdated_version')

    def on_existing_user(self, user):
        self.calls.append('on_existing_user')

    def on_wrong_user_password(self, attempt):
        self.calls.append(f'on_wrong_user_password:{attempt}')

    def on_user_password_reset(self):
        self.calls.append('on_user_password_reset')

    def on_master_password_wrong(self):
        self.calls.append('on_master_password_wrong')

    def on_connection_issue(self, error):
        self.calls.append('on_connection_issue')

    def on_provisioned(self):
        self.calls.append('on_provisioned')

    def on_gave_up(self):
        self.calls.append('on_gave_up')


@pytest.fixture
def secret_dir(tmp_path):
    config_dir = tmp_path / 'config'
    (config_dir / '.secret').mkdir(parents=True)
    return str(config_dir)


# --- URL builders ---------------------------------------------------------------------

def test_connection_url_quotes_the_password():
    url = db_setup.connection_url('u', 'p@s s', 'h', 5432, 'd')
    assert url == 'postgres://u:p%40s%20s@h:5432/d?sslmode=prefer'


def test_config_connection_url_uses_the_secret_placeholder():
    url = db_setup.config_connection_url('u', 'h', 5432, 'd')
    # the real password is stored in config/.secret and substituted for the placeholder
    assert url.startswith('postgres://u:')
    assert url.endswith('@h:5432/d?sslmode=prefer')
    assert db_setup._PASSWORD_PLACEHOLDER in url


@pytest.mark.parametrize("database", ["a?b", "a#b", "a/b"])
def test_the_database_name_is_percent_encoded_so_the_path_survives(database):
    """``?``/``#``/``/`` must not become a query, a fragment or a path separator.

    The bot decides a federation by comparing ``urlparse(...).path`` of the two stored URLs, so an
    unencoded name would parse to a DIFFERENT path at boot than the name it claims to name.
    """
    from urllib.parse import quote, urlparse

    url = db_setup.config_connection_url('u', 'h', 5432, database)
    parsed = urlparse(url)
    assert parsed.path == "/" + quote(database, safe="")
    assert parsed.query == "sslmode=prefer"      # the sslmode, never a slurped database name
    assert parsed.fragment == ""
    # the live builder quotes the same way, so both stored and connecting URLs agree
    assert urlparse(db_setup.connection_url('u', 'p', 'h', 5432, database)).path == parsed.path


def test_the_user_is_percent_encoded_in_both_builders():
    from urllib.parse import quote

    for url in (db_setup.config_connection_url('a@b:c', 'h', 5432, 'd'),
                db_setup.connection_url('a@b:c', 'p', 'h', 5432, 'd')):
        assert f"postgres://{quote('a@b:c')}:" in url


def test_a_normal_name_is_unchanged_by_the_encoding():
    """The encoding is the identity on installer-shaped names, so nothing already written moves."""
    assert db_setup.config_connection_url('dcsserverbot', '127.0.0.1', 5432, 'dcsserverbot') == \
        "postgres://dcsserverbot:SECRET@127.0.0.1:5432/dcsserverbot?sslmode=prefer"


def test_a_slash_in_the_user_cannot_end_the_netloc_early():
    """``quote``'s default ``safe='/'`` would keep a ``/`` in the user, ending the netloc at ``a``.

    ``postgres://a/b:pw@127.0.0.1:5432/db`` parses to netloc ``a`` and path ``/b:pw@127.0.0.1:5432/db``
    — a different host and a different database. Both builders must encode the ``/``.
    """
    from urllib.parse import quote, urlparse

    for url in (db_setup.config_connection_url("a/b", "127.0.0.1", 5432, "db"),
                db_setup.connection_url("a/b", "pw", "127.0.0.1", 5432, "db")):
        parsed = urlparse(url)
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port == 5432
        assert parsed.path == "/db"
        assert parsed.username == quote("a/b", safe="")


def test_a_slash_in_the_password_cannot_break_the_live_url():
    """The live builder dials with the typed password: an encoded ``/``/``?``/``#`` must survive."""
    from urllib.parse import urlparse

    parsed = urlparse(db_setup.connection_url("u", "p/a?b#q", "127.0.0.1", 5432, "db"))
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 5432
    assert parsed.path == "/db"
    assert parsed.query == "sslmode=prefer"
    assert parsed.fragment == ""
    assert parsed.password == "p%2Fa%3Fb%23q"


# --- the master user's safe set -------------------------------------------------------

@pytest.mark.parametrize("user", ["postgres", "dcsserverbot", "a_b-1", "A1", "9"])
def test_a_safe_master_user_is_accepted(user):
    assert db_setup.is_valid_master_user(user) is True


@pytest.mark.parametrize("user", ["", ".", "..", "a/b", "/etc/passwd", "a.b", "X.clusterdb",
                                  "a b", "äö", "a\\b", "a\n", "a\t"])
def test_an_unsafe_master_user_is_refused(user):
    """The name becomes a ``config/.secret/<key>.pkl`` file and a URL userinfo component."""
    assert db_setup.is_valid_master_user(user) is False


@pytest.mark.parametrize("user", ["database", "clusterdb", "DCS", "dcs", "proxy", "token", "hash",
                                  "discord_client_secret", "Database", "CLUSTERDB", "Token"])
def test_a_reserved_master_user_is_refused(user):
    """A master user is stored under its OWN name in ``config/.secret/<master_user>.pkl``, so a name
    equal to one of the bot's other secret keys would overwrite that secret (``database.pkl``,
    ``clusterdb.pkl`` ...) — the same collision class the ``.`` ban closes for ``<user>.clusterdb``."""
    assert db_setup.is_valid_master_user(user) is False
    assert db_setup.is_reserved_master_user(user) is True


@pytest.mark.parametrize("user", ["postgres", "dcsserverbot", "a_b-1", "A1", "9", "database2",
                                  "clusterdbx", "tokendb"])
def test_a_non_reserved_master_user_is_not_flagged(user):
    """Ordinary names — the installer/tour default ``postgres`` included — are not reserved. ``postgres``
    IS a ``.secret`` key (the master's own slot, and ``restore.py``'s), but it is the master user by
    design, so it stays accepted; the guard lists only the bot's OTHER secret files."""
    assert db_setup.is_reserved_master_user(user) is False


def test_the_reserved_set_is_the_bot_s_own_secret_file_names():
    """The complete set of names that can occupy ``config/.secret/<key>.pkl`` for the bot's own keys."""
    assert db_setup.RESERVED_SECRET_KEYS == frozenset({
        "database", "clusterdb", "dcs", "proxy", "token", "hash", "discord_client_secret"})


def test_the_reserved_comparison_is_case_insensitive():
    """The key names a file: refusing case-insensitively closes the collision on Windows/NTFS too."""
    for user in ("Database", "DATABASE", "ClusterDB", "Discord_Client_Secret"):
        assert db_setup.is_reserved_master_user(user) is True
        assert db_setup.is_valid_master_user(user) is False



# --- version classification -----------------------------------------------------------

@pytest.mark.parametrize("version,outdated", [('13.9', True), ('14.0', False), ('16.2', False)])
def test_version_classification(version, outdated):
    assert db_setup.is_version_outdated(version) is outdated


def test_get_server_version_reads_the_installer_query():
    conn = FakeConn(version='15.4')
    assert db_setup.get_server_version(conn) == '15.4'
    assert 'server_version_num' in conn.statements[0]


# --- individual DDL statements --------------------------------------------------------

def test_create_user_statement_is_the_installers():
    conn = FakeConn()
    db_setup.create_user(conn, 'botuser', 'pw')
    assert 'CREATE USER' in conn.statements[0]
    assert 'WITH ENCRYPTED PASSWORD' in conn.statements[0]


def test_reset_user_password_statement():
    conn = FakeConn()
    db_setup.reset_user_password(conn, 'botuser', 'pw')
    assert 'ALTER USER' in conn.statements[0]
    assert 'WITH ENCRYPTED PASSWORD' in conn.statements[0]


def test_create_user_raises_when_the_role_exists():
    conn = FakeConn(user_exists=True)
    with pytest.raises(psycopg.Error):
        db_setup.create_user(conn, 'botuser', 'pw')


def test_create_database_returns_true_and_runs_grant_and_owner():
    conn = FakeConn()
    assert db_setup.create_database(conn, 'botdb', 'botuser') is True
    assert conn.statements_matching('CREATE DATABASE')
    assert conn.statements_matching('GRANT ALL PRIVILEGES ON DATABASE')
    assert conn.statements_matching('ALTER DATABASE')


def test_create_database_returns_false_when_it_already_exists():
    conn = FakeConn(database_exists=True)
    assert db_setup.create_database(conn, 'botdb', 'botuser') is False


# --- provisioning orchestration -------------------------------------------------------

def test_provision_creates_user_and_database(monkeypatch, secret_dir):
    conn = FakeConn()
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)
    io = FakeIO()
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result is not None
    assert result.created is True
    assert result.existing_user is False
    assert result.password_reset is False
    assert db_setup._PASSWORD_PLACEHOLDER in result.url
    assert 'on_provisioned' in io.calls
    # the bot password is stored so it survives a restart
    assert (Path(secret_dir) / '.secret' / 'database.pkl').exists()


def test_provision_reports_an_existing_database(monkeypatch, secret_dir):
    conn = FakeConn(database_exists=True)
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, FakeIO())
    assert result.created is False


def test_provision_outdated_version_is_reported(monkeypatch, secret_dir):
    conn = FakeConn(version='12.4')
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)
    io = FakeIO()
    provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert 'on_outdated_version' in io.calls


def test_provision_existing_user_accepts_the_given_password(monkeypatch, secret_dir):
    conn = FakeConn(user_exists=True)
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)
    # the first bot-user password verifies
    monkeypatch.setattr(db_setup, 'verify_login', lambda *a, **k: True)
    io = FakeIO(user_passwords=['right'])
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result.existing_user is True
    assert result.password_reset is False
    assert 'on_existing_user' in io.calls
    # no ALTER USER happened
    assert not conn.statements_matching('ALTER USER')


def test_provision_existing_user_resets_after_three_wrong_passwords(monkeypatch, secret_dir):
    conn = FakeConn(user_exists=True)
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)
    monkeypatch.setattr(db_setup, 'verify_login', lambda *a, **k: False)
    io = FakeIO(user_passwords=['a', 'b', 'c'])
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result.password_reset is True
    assert [c for c in io.calls if c.startswith('on_wrong_user_password')] == [
        'on_wrong_user_password:1', 'on_wrong_user_password:2', 'on_wrong_user_password:3']
    assert 'on_user_password_reset' in io.calls
    assert conn.statements_matching('ALTER USER')


def test_provision_retries_on_a_wrong_master_password(monkeypatch, secret_dir):
    calls = {'n': 0}
    good = FakeConn()

    def connect(url, **kw):
        calls['n'] += 1
        if calls['n'] == 1:
            raise psycopg.errors.InvalidPassword('nope')
        return good

    monkeypatch.setattr(db_setup.psycopg, 'connect', connect)
    io = FakeIO(master_passwords=['wrong', 'right'])
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result is not None
    assert 'on_master_password_wrong' in io.calls
    assert calls['n'] == 2


def test_provision_gives_up_after_three_connect_issues(monkeypatch, secret_dir):
    def connect(url, **kw):
        raise psycopg.OperationalError('down')

    monkeypatch.setattr(db_setup.psycopg, 'connect', connect)
    io = FakeIO(master_passwords=['a', 'b', 'c'])
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result is None
    assert io.calls.count('on_connection_issue') == 3
    assert io.calls[-1] == 'on_gave_up'


def test_provision_reuses_a_stored_master_password(monkeypatch, secret_dir):
    from core import utils
    utils.set_password('postgres', 'stored', secret_dir)
    conn = FakeConn()
    monkeypatch.setattr(db_setup.psycopg, 'connect', lambda *a, **k: conn)

    class IO(FakeIO):
        def master_password(self, master_user):
            raise AssertionError('a stored master password must not prompt')

    io = IO()
    result = provision_database('127.0.0.1', 5432, 'botuser', 'botdb', secret_dir, io)
    assert result is not None
