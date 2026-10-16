"""PostgreSQL provisioning for the bot's database and its own role.

Both the installer (``install.py``) and the web setup tour need to reach a PostgreSQL server,
create the bot's role and database, and classify the driver's errors the same way. Nothing here
reads stdin, prints, or translates: the caller passes the interactive decisions through a
:class:`DatabaseProvisionIO` and owns the transport (terminal or HTTP).

The statements and the retry/recovery sequence are the ones the installer has always run.
"""
from __future__ import annotations

import os
import re
import secrets
from urllib.parse import quote, unquote

import psycopg
from packaging.version import parse
from psycopg import sql

from core import utils

__all__ = [
    "MIN_SERVER_VERSION_MAJOR",
    "DEFAULT_MASTER_DATABASE",
    "DEFAULT_MASTER_USER",
    "RESERVED_SECRET_KEYS",
    "DatabaseProvisionIO",
    "ProvisionResult",
    "connection_url",
    "config_connection_url",
    "requote",
    "rebuild_url",
    "is_valid_master_user",
    "is_reserved_master_user",
    "get_server_version",
    "is_version_outdated",
    "create_user",
    "reset_user_password",
    "verify_login",
    "create_database",
    "provision_database",
]

MIN_SERVER_VERSION_MAJOR = 14
DEFAULT_MASTER_DATABASE = 'postgres'
DEFAULT_MASTER_USER = 'postgres'

# The names the bot stores its OWN secrets under in ``config/.secret/<key>.pkl``. A master user
# carrying one of them would overwrite that secret's file, so it is refused (case-insensitively,
# closing the collision on case-folding file systems too). ``postgres`` is the master user by
# design and is not one of the bot's own keys.
RESERVED_SECRET_KEYS = frozenset({
    'database', 'clusterdb', 'dcs', 'proxy', 'token', 'hash', 'discord_client_secret'
})

# A master user becomes a ``config/.secret/<user>.pkl`` file name and a URL userinfo component:
# letters, digits, ``_`` and ``-`` only. An empty name, a dot, a slash or any other character is out.
_MASTER_USER_RE = re.compile(r'[A-Za-z0-9_-]+')

# Placeholder written instead of the password in the stored connection URL. ``rename_node`` and the
# bot's config loader substitute it with the value from ``config/.secret``.
_PASSWORD_PLACEHOLDER = 'SECRET'

# The version probe the installer runs. Major.minor is derived the same way: the numeric server
# version divided into major and the trailing minor.
_SERVER_VERSION_SQL = """
    SELECT (current_setting('server_version_num')::int / 10000) 
           || '.' || 
           (current_setting('server_version_num')::int % 100) AS version
"""


def is_valid_master_user(user: str) -> bool:
    """True when *user* is safe as a ``config/.secret/<user>.pkl`` file name and a URL userinfo
    component: letters, digits, ``_`` and ``-`` only, and not one of the bot's reserved secret
    keys."""
    return bool(_MASTER_USER_RE.fullmatch(user)) and not is_reserved_master_user(user)


def is_reserved_master_user(user: str) -> bool:
    """True when *user* equals one of the bot's own ``config/.secret`` keys (case-insensitive):
    storing the master password under it would overwrite that secret."""
    return user.lower() in RESERVED_SECRET_KEYS


def requote(value: str, *, safe: str = "") -> str:
    """Normalize a userinfo or path component read back out of ``urlparse`` for re-insertion into a
    URL.

    ``urlparse`` hands the component back still percent-encoded, so ``unquote`` followed by
    ``quote`` leaves an already-encoded value byte-identical while encoding any raw ``?``/``#``/
    ``/``/``@``/``:`` it still carries. *safe* keeps the separators a caller needs (``/`` for a
    path).
    """
    return quote(unquote(value), safe=safe)


def rebuild_url(url, *, host: str, port: int, password: str | None = None) -> str:
    """Rebuild a connection URL from a ``urlparse`` result against *host* and *port*, keeping the
    userinfo and the database path percent-encoded.

    *password* is the userinfo password to write; omitted, the URL's own password is kept. The
    config carriers pass the ``SECRET`` placeholder instead, because the real password lives in
    ``config/.secret``.
    """
    if password is None:
        password = url.password
    # the path is ``/`` + the (encoded) database name: quote each segment, keep the separators
    path = "/".join(requote(segment) for segment in url.path.split("/"))
    return (f"{url.scheme}://{requote(url.username or '')}:{requote(password or '')}"
            f"@{host}:{port}{path}?sslmode=prefer")


def connection_url(user: str, password: str, host: str, port: int, database: str) -> str:
    return (f"postgres://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
            f"/{quote(database, safe='')}?sslmode=prefer")


def config_connection_url(user: str, host: str, port: int, database: str) -> str:
    """The URL stored in the config: the password is the literal ``SECRET`` placeholder that
    ``rename_node`` substitutes with the real password from ``config/.secret``."""
    return (f"postgres://{quote(user, safe='')}:{_PASSWORD_PLACEHOLDER}@{host}:{port}"
            f"/{quote(database, safe='')}?sslmode=prefer")


def get_server_version(conn) -> str:
    cursor = conn.execute(_SERVER_VERSION_SQL)
    return cursor.fetchone()[0]


def is_version_outdated(version: str) -> bool:
    return parse(version).major < MIN_SERVER_VERSION_MAJOR


def create_user(conn, user: str, password: str) -> None:
    """CREATE the bot's role. Raises ``psycopg.Error`` when the role already exists -- the caller
    decides how to recover from that."""
    conn.execute(
        sql.SQL("CREATE USER {} WITH ENCRYPTED PASSWORD {}")
        .format(sql.Identifier(user), sql.Literal(password))
    )


def reset_user_password(conn, user: str, password: str) -> None:
    conn.execute(
        sql.SQL("ALTER USER {} WITH ENCRYPTED PASSWORD {}")
        .format(sql.Identifier(user), sql.Literal(password))
    )


def verify_login(user: str, password: str, host: str, port: int, database: str) -> bool:
    """True when *user* can authenticate against *database* with *password*."""
    try:
        with psycopg.connect(connection_url(user, password, host, port, database)):
            return True
    except psycopg.Error:
        return False


def create_database(conn, database: str, owner: str) -> bool:
    """Create *database*, grant it to *owner* and hand it over.

    Returns True when the database was created, False when it already existed (the driver's error
    is swallowed the way the installer has always swallowed it). The outcome is returned instead of
    hidden, so the caller can report an existing database rather than implying it was created.
    """
    try:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        conn.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}")
            .format(sql.Identifier(database), sql.Identifier(owner))
        )
        conn.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}")
            .format(sql.Identifier(database), sql.Identifier(owner))
        )
        return True
    except psycopg.Error:
        return False


class DatabaseProvisionIO:
    """The caller-supplied interactive decisions and progress reports.

    The three ``*_password``/``credentials`` methods must return a value; the ``on_*`` methods are
    notifications and default to no-ops. The installer implements them with gettext'd prompts, the
    web tour with form fields and page notices.
    """

    def master_credentials(self) -> tuple[str, str]:
        """Return ``(master_database, master_user)``."""
        raise NotImplementedError

    def master_password(self, master_user: str) -> str:
        raise NotImplementedError

    def bot_user_password(self, user: str) -> str:
        raise NotImplementedError

    def on_outdated_version(self) -> None:
        pass

    def on_existing_user(self, user: str) -> None:
        pass

    def on_wrong_user_password(self, attempt: int) -> None:
        pass

    def on_user_password_reset(self) -> None:
        pass

    def on_master_password_wrong(self) -> None:
        pass

    def on_connection_issue(self, error: Exception) -> None:
        pass

    def on_provisioned(self) -> None:
        pass

    def on_gave_up(self) -> None:
        pass


class ProvisionResult:
    """What :func:`provision_database` produced.

    *url* is the masked URL to write to the config; *created* is False when the database already
    existed (the tour states this, the installer keeps its unconditional message);
    *existing_user*/*password_reset* describe the recovery branch.
    """

    __slots__ = ("url", "created", "existing_user", "password_reset")

    def __init__(self, url: str, *, created: bool, existing_user: bool, password_reset: bool):
        self.url = url
        self.created = created
        self.existing_user = existing_user
        self.password_reset = password_reset


def provision_database(host: str, port: int, user: str, database: str, config_dir: str,
                       io: DatabaseProvisionIO) -> ProvisionResult | None:
    """Connect as the master user, create the bot's role and database, store the passwords.

    Walks the installer's master-password retry (three tries, a stored password preferred to a
    prompt, the stored master password deleted on a wrong one) and its existing-role recovery (ask
    for the bot user's password up to three times, otherwise reset it). Returns None after three
    failed connects.
    """
    for _tries in range(1, 4):
        master_db, master_user = io.master_credentials()
        try:
            master_passwd = utils.get_password(master_user, config_dir)
        except ValueError:
            master_passwd = io.master_password(master_user)
        url = connection_url(master_user, master_passwd, host, port, master_db)
        try:
            created = False
            existing_user = False
            password_reset = False
            with psycopg.connect(url, autocommit=True) as conn:
                utils.set_password(master_user, master_passwd, config_dir)
                try:
                    passwd = utils.get_password('database', config_dir) or ''
                except ValueError:
                    passwd = secrets.token_urlsafe(8)
                try:
                    version = get_server_version(conn)
                    if is_version_outdated(version):
                        io.on_outdated_version()
                    create_user(conn, user, passwd)
                except psycopg.Error:
                    existing_user = True
                    io.on_existing_user(user)
                    passwd, password_reset = _recover_existing_user(
                        conn, user, host, port, database, io)
                utils.set_password('database', passwd, config_dir)
                created = create_database(conn, database, user)
                io.on_provisioned()
            return ProvisionResult(config_connection_url(user, host, port, database),
                                   created=created, existing_user=existing_user,
                                   password_reset=password_reset)
        except psycopg.errors.InvalidPassword:
            io.on_master_password_wrong()
            pkl_file = os.path.join(config_dir, '.secret', master_user + '.pkl')
            if os.path.exists(pkl_file):
                os.remove(pkl_file)
        except psycopg.OperationalError as ex:
            io.on_connection_issue(ex)
    io.on_gave_up()
    return None


def _recover_existing_user(conn, user: str, host: str, port: int, database: str,
                           io: DatabaseProvisionIO) -> tuple[str, bool]:
    """The bot's role already exists: keep its password if it verifies, otherwise reset it.

    Returns ``(password, reset)``.
    """
    for attempt in range(1, 4):
        passwd = io.bot_user_password(user)
        if verify_login(user, passwd, host, port, database):
            return passwd, False
        io.on_wrong_user_password(attempt)
    io.on_user_password_reset()
    passwd = secrets.token_urlsafe(8)
    reset_user_password(conn, user, passwd)
    return passwd, True
