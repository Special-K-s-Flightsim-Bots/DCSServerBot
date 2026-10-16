"""Step 3 of the tour — the Database step: the two paths, the per-path test, the failure states.

ONE SCREEN, TWO PATHS. ``CREATE`` ("Create a new database") makes the bot's own database and role
from the PostgreSQL superuser's credentials; ``EXISTING`` ("Use an existing database") points the bot
at a database that already exists, with the bot's OWN credentials. The operator's choice decides
which fields are shown and which statements may run — never a second page.

PATH A IS ALLOWED TO WRITE, PATH B IS NOT. ``CREATE``'s test is exactly the DDL the installer runs
(through :mod:`core.utils.db_setup`, so there is one implementation of the SQL): create the role,
create the database, store the passwords. ``EXISTING``'s test is READ-ONLY: it connects as the bot's
own user and inspects the database; it never creates, alters or drops anything. There is no
clean/reset action on either path — a database is only ever created, never dropped.

THE CONTROL FLOW IS THE FRONT END'S, THE SQL IS SHARED. The installer's retry loops are blocking
stdin reads; here one form submission is one attempt, so the counters live in the tour's answers
(:class:`DatabaseAnswers`) while every statement goes through the shared helpers in
:mod:`core.utils.db_setup`. The one parity rule that survives verbatim: a role that already exists
is asked for its password up to :data:`MAX_USER_PASSWORD_ATTEMPTS` times, and after the last wrong
answer the password is RESET (``ALTER USER … WITH ENCRYPTED PASSWORD``) and stored in
``config/.secret/``.

FAILURE STATES ARE CLASSIFIED. A raw driver string never reaches the page: every outcome is one of
the codes in :class:`TestOutcome`, and the tour owns the operator-facing sentence for each.

SECRETS NEVER ENTER THE DRAFT. Passwords are held only for the process lifetime (:func:`secret_store`),
never written to the draft file and never rendered back into a field.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import ipaddress
import logging
import re
import secrets
import socket
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from urllib.parse import unquote, urlparse

import psycopg
from packaging.version import parse

from core import utils
from core.utils import db_setup

__all__ = [
    "CREATE",
    "EXISTING",
    "PATHS",
    "MAX_USER_PASSWORD_ATTEMPTS",
    "MIN_SERVER_VERSION_MAJOR",
    "CONNECT_TIMEOUT",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MASTER_DATABASE",
    "MASTER_USER",
    "DEFAULT_DATABASE",
    "DEFAULT_USER",
    "DATABASE_SECRET_KEY",
    "CLUSTER_SECRET_KEY",
    "FEDERATION_KEY",
    "CLUSTER_KEY",
    "SAME_DATABASE_CODE",
    "DatabaseAnswers",
    "TestOutcome",
    "answers_from_mapping",
    "non_secret_document",
    "prefill_document",
    "is_loopback_host",
    "cluster_database_url",
    "federation_signal",
    "is_federated",
    "signature",
    "verification",
    "TESTED_KEY",
    "mark_tested",
    "clear_tested",
    "tested_here",
    "same_database",
    "master_secret_key",
    "LOCAL_POSTGRES_PORT",
    "LOCAL_PROBE_TIMEOUT",
    "local_postgres_open",
    "cached_local_postgres",
    "recognised_tables",
    "current_schema_version",
    "secret_store",
    "run_test",
]

log = logging.getLogger(__name__)

#: The two choices of step 3.
CREATE = "create"
EXISTING = "existing"
PATHS = (CREATE, EXISTING)

#: The installer's own defaults (``core/commandline.py``: ``-u``/``-d`` default to ``dcsserverbot``;
#: the master connection defaults to the ``postgres`` maintenance database as ``postgres``).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5432
MASTER_DATABASE = db_setup.DEFAULT_MASTER_DATABASE
MASTER_USER = db_setup.DEFAULT_MASTER_USER
DEFAULT_DATABASE = "dcsserverbot"
DEFAULT_USER = "dcsserverbot"

#: The installer's role-recovery bound (``install.py:138-171`` / ``db_setup._recover_existing_user``).
MAX_USER_PASSWORD_ATTEMPTS = 3

#: The oldest server version the bot supports — the amber warning's threshold (never a block).
MIN_SERVER_VERSION_MAJOR = db_setup.MIN_SERVER_VERSION_MAJOR

#: The most a single connection attempt may wait, in seconds — the bound a Test button may impose.
CONNECT_TIMEOUT = 5

#: The local loopback port the Database step looks for before it offers any install help: the
#: installer's own default port. Probed on ``DEFAULT_HOST`` (this machine) only — a server on another
#: HOST leaves this port closed here, so the answer only decides whether the nudge is shown.
LOCAL_POSTGRES_PORT = DEFAULT_PORT

#: The most a single port probe may wait, in seconds. A connect to a FILTERED port is a SYN that is
#: never answered, so the socket's own timeout is the bound — a page render never hangs on it.
LOCAL_PROBE_TIMEOUT = 0.5

#: The process store's two keys for the typed passwords (see :func:`secret_store`).
MASTER_PASSWORD_KEY = "master_password"
BOT_PASSWORD_KEY = "bot_password"

#: The process store's third key: the fingerprint a Test that SUCCEEDED in THIS process left behind.
#: It is what makes the green light belong to the run and not to the draft — a ``verified``/``signature``
#: restored from a draft (a fresh process, an empty store) cannot reopen the gate, so a restarted
#: console must test again.
TESTED_KEY = "tested"

#: The two secret keys the installer's store uses for a database block: ``"database"`` for the local
#: block (``nodes.yaml``) and ``"clusterdb"`` for the cluster block (``main.yaml``, the key
#: ``nodeimpl.get_database_urls`` reads at ``config/.secret/clusterdb.pkl``). A block's answers carry
#: its key, so the same machinery writes the right file.
DATABASE_SECRET_KEY = "database"
CLUSTER_SECRET_KEY = "clusterdb"

#: The draft's ``database`` sub-keys the cluster block and its tick live under. Named here so the
#: render and the submit of step 3 cannot disagree about where the second block is kept.
FEDERATION_KEY = "federation"
CLUSTER_KEY = "cluster"

#: The classified outcome a cluster test returns when its database name equals the local block's: the
#: bot decides a federation by database NAME, so this is allowed after an amber warning (never a
#: block) — writing it is a configuration that is honestly no federation.
SAME_DATABASE_CODE = "ok_same_database"


# ------------------------------------------------------------------------------- the answers

@dataclass
class DatabaseAnswers:
    """Step 3's answers: the path, the shared connection fields and the two blocks' names.

    ``database`` and ``user`` serve BOTH paths — they are the installer's ``-d``/``-u`` names on the
    create path and the bot's connection target on the existing path — so toggling the choice keeps
    them, exactly as host/port are never cleared.

    ``secret_key`` is which :data:`config/.secret` key a password of THIS block belongs to —
    :data:`DATABASE_SECRET_KEY` for the local block (the default, today's behaviour) and
    :data:`CLUSTER_SECRET_KEY` for the cluster block. It never reaches the draft and never appears in
    the rendered form; it only picks the store slot and the secret file :func:`run_test` writes.
    """

    path: str = CREATE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    master_database: str = MASTER_DATABASE
    master_user: str = MASTER_USER
    database: str = DEFAULT_DATABASE
    user: str = DEFAULT_USER
    #: how many wrong answers the existing role's password has been given so far
    user_password_attempts: int = 0
    #: the store key and secret file this block's passwords belong to (see the class docstring)
    secret_key: str = DATABASE_SECRET_KEY

    @property
    def is_create(self) -> bool:
        return self.path == CREATE


def _as_path(value) -> str:
    return value if value in PATHS else CREATE


def _as_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def answers_from_mapping(data: Mapping | None, *,
                         secret_key: str = DATABASE_SECRET_KEY) -> DatabaseAnswers:
    """Build the answers from a draft ``database`` block (missing keys get the installer's
    defaults). *secret_key* names which block's secret file the block's passwords belong to."""
    data = data if isinstance(data, Mapping) else {}
    return DatabaseAnswers(
        path=_as_path(data.get("path")),
        host=str(data.get("host") or DEFAULT_HOST),
        port=_as_port(data.get("port")),
        master_database=str(data.get("master_database") or MASTER_DATABASE),
        master_user=str(data.get("master_user") or MASTER_USER),
        database=str(data.get("database") or DEFAULT_DATABASE),
        user=str(data.get("user") or DEFAULT_USER),
        user_password_attempts=int(data.get("user_password_attempts") or 0),
        secret_key=secret_key,
    )


def non_secret_document(answers: DatabaseAnswers) -> dict:
    """The draft-writable part of the answers (no password ever appears here)."""
    return {
        "path": answers.path,
        "host": answers.host,
        "port": answers.port,
        "master_database": answers.master_database,
        "master_user": answers.master_user,
        "database": answers.database,
        "user": answers.user,
        "user_password_attempts": answers.user_password_attempts,
    }


def is_loopback_host(host) -> bool:
    """Whether *host* names this machine's own loopback interface (``localhost``, ``127.0.0.0/8``).

    The whole ``127.0.0.0/8`` block counts, not the single spelling ``127.0.0.1``: ``127.0.0.2`` and
    the rest of the block are loopback too, and a string comparison would miss them. An address that
    does not parse is not loopback (a hostname may well name another machine).
    """
    text = str(host or "").strip().lower()
    if not text:
        return False
    if text == "localhost" or text.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def federation_signal(config_dir) -> Path:
    """The path whose presence the bot reads as "this install is federated".

    Exactly ``config/.secret/clusterdb.pkl``: ``nodeimpl.get_database_urls`` reads that path for
    the cluster pool's password and treats a federation as ``apool != cpool``, so a ``.pkl`` there
    is the bot's own statement that this installation has a cluster database.
    """
    return Path(config_dir) / ".secret" / "clusterdb.pkl"


def is_federated(config_dir) -> bool:
    """Whether this installation is federated — the bot's own signal, not a re-derived one.

    ``nodeimpl.get_database_urls`` decides a federation by the presence of
    ``config/.secret/clusterdb.pkl``; asking the same file the same question keeps the pre-fill's
    decision and the bot's decision from ever diverging. A missing config dir is not federated.
    """
    return bool(config_dir) and federation_signal(config_dir).exists()


def _node_database_url(nodes) -> str | None:
    """A node's OWN URL out of ``nodes.yaml``, PREFERRING one whose host is not loopback.

    A ``127.0.0.1`` URL in a node's block names the machine that WROTE it, which on a joining
    machine is itself, so a non-loopback one is taken first. The first node URL is still returned
    when every one of them is loopback: a single-machine install has nowhere else to point.
    """
    if not isinstance(nodes, Mapping):
        return None
    first: str | None = None
    for node in nodes.values():
        block = node.get("database") if isinstance(node, Mapping) else None
        url = block.get("url") if isinstance(block, Mapping) else None
        if not url:
            continue
        url = str(url)
        if first is None:
            first = url
        if not is_loopback_host(urlparse(url).hostname):
            return url
    return first


def cluster_database_url(main, nodes, config_dir) -> str | None:
    """The connection URL a JOINING node's database step should be pre-filled with, or ``None``.

    A node's OWN database — ``nodes.yaml``'s per-node ``database.url`` — is preferred, and among a
    node's URLs one whose host is not loopback first (see :func:`_node_database_url`).

    ``main.yaml``'s ``database.url`` is the CLUSTER database (``nodeimpl.get_database_urls`` reads
    it as the cluster pool), so it is taken ONLY when no node block carries a URL AND the install is
    NOT federated (:func:`is_federated`). A node database can never be the federation database: on a
    federated install whose ``nodes.yaml`` carries no URL the answer is ``None`` — an empty field is
    answered, a wrong database is not.
    """
    url = _node_database_url(nodes)
    if url:
        return url
    if is_federated(config_dir):
        return None
    block = main.get("database") if isinstance(main, Mapping) else None
    url = block.get("url") if isinstance(block, Mapping) else None
    return str(url) if url else None


def prefill_document(url) -> dict:
    """The draft's ``database`` block pre-filled from *url*, on the ``EXISTING`` path.

    Every connection field is filled from the URL the install already names — the user and the
    database name are percent-decoded, since that is how the config writer encodes them — so the one
    field the operator still types on step 3 is the password (which is process-memory only and can
    never be pre-filled).
    """
    parsed = urlparse(str(url or ""))
    answers = DatabaseAnswers(
        path=EXISTING,
        host=parsed.hostname or DEFAULT_HOST,
        port=_as_port(parsed.port),
        database=unquote(parsed.path.lstrip("/")) or DEFAULT_DATABASE,
        user=unquote(parsed.username or "") or DEFAULT_USER,
    )
    return non_secret_document(answers)


def signature(answers: DatabaseAnswers, master_password: str, bot_password: str) -> str:
    """The fingerprint of everything a successful test proved.

    Editing any field — or switching the path — changes it, which is what re-disables Continue: the
    green light belongs to the VALUES, not to the screen.
    """
    canonical = "|".join((
        answers.path, answers.host, str(answers.port), answers.master_database, answers.master_user,
        answers.database, answers.user, master_password or "", bot_password or "",
    ))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verification(document: Mapping | None, *, secret_key: str = DATABASE_SECRET_KEY,
                 app=None) -> bool:
    """Whether a draft database block still test-verifies: ``verified`` AND a matching fingerprint.

    *document* is a draft block (the local ``database`` mapping, or the ``database.cluster`` one);
    *secret_key* names the process store its passwords were typed into. Recomputing the fingerprint
    from the CURRENT values is what makes an edit after a green light re-close the gate — the same
    rule ``step 3``'s Continue has always applied to the one block, applied to a block by its key.
    """
    if not isinstance(document, Mapping) or not document.get("verified"):
        return False
    answers = answers_from_mapping(document, secret_key=secret_key)
    store = secret_store(app, secret_key)
    return document.get("signature") == signature(answers, store.get(MASTER_PASSWORD_KEY, ""),
                                                  store.get(BOT_PASSWORD_KEY, ""))


def mark_tested(app, signature_value: str, *, secret_key: str = DATABASE_SECRET_KEY) -> None:
    """Record that a Test with exactly *signature_value* passed in THIS process."""
    secret_store(app, secret_key)[TESTED_KEY] = signature_value


def clear_tested(app, *, secret_key: str = DATABASE_SECRET_KEY) -> None:
    """Forget this process's passed Test for the block (a failed test, or none yet)."""
    secret_store(app, secret_key).pop(TESTED_KEY, None)


def tested_here(app, signature_value: str, *, secret_key: str = DATABASE_SECRET_KEY) -> bool:
    """Whether a Test with exactly *signature_value* passed in THIS process.

    The proof lives in the process store, so it is empty after a restart (and after any failed test):
    a ``verified``/``signature`` restored from a draft — the draft's own fingerprint — cannot reopen
    the gate. A changed value set recomputes to a different signature and closes it exactly as an edit
    does, so Continue is offered only for the values a Test passed on, in the run that passed it.
    """
    if not signature_value:
        return False
    return secret_store(app, secret_key).get(TESTED_KEY) == signature_value


def same_database(local: DatabaseAnswers, cluster: DatabaseAnswers) -> bool:
    """True when the cluster block names the SAME database as the local block.

    The bot decides a federation by database NAME (``nodeimpl.get_database_urls``), so an equal name
    is no federation at all — the step warns and lets it through, and the Review screen says so.
    """
    return bool(local.database) and local.database == cluster.database


def master_secret_key(answers: DatabaseAnswers) -> str:
    """The ``config/.secret`` key a block's MASTER password is stored under.

    The local block keeps the installer's key — the master user alone — so a later installer run
    finds the password it stored. The cluster block gets its OWN key, so a cluster create-test can
    never overwrite (or, on a refused password, delete) the local block's master password, even when
    both name the same master user on different hosts.
    """
    if answers.secret_key == CLUSTER_SECRET_KEY:
        return f"{answers.master_user}.{CLUSTER_SECRET_KEY}"
    return answers.master_user


# ------------------------------------------------------------------------------- the process store

def secret_store(app, secret_key: str = DATABASE_SECRET_KEY) -> dict:
    """The per-application dict that holds the typed passwords for this process's lifetime.

    Partitioned by *secret_key*, so the local block's passwords and the cluster block's passwords
    never share a slot (``"database"`` vs ``"clusterdb"``). The default returns the local block's
    partition — today's one store.

    Not the session cookie (``SessionMiddleware`` signs but does not encrypt, so a cookie would put
    the password in the browser) and not the draft file (plain text on disk). Lost on restart, the
    same lifetime the ephemeral session key already has.
    """
    stores = getattr(app.state, "setup_db_secrets", None)
    if not isinstance(stores, dict):
        stores = {}
        app.state.setup_db_secrets = stores
    partition = stores.get(secret_key)
    if not isinstance(partition, dict):
        partition = {}
        stores[secret_key] = partition
    return partition


# ------------------------------------------------------------------------------- the port probe

def _probe_local_postgres(timeout: float) -> bool | None:
    """Connect once to the local PostgreSQL port, bounded by *timeout*.

    ``True`` when the port ACCEPTS a connection, ``False`` when it REFUSES one (nothing is listening),
    ``None`` when there is no answer in time (a filtered port) or any other error — the caller shows
    the install help for everything but a proven ``True``.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((DEFAULT_HOST, LOCAL_POSTGRES_PORT))
        except OSError as ex:
            if ex.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
                return False
            return None
        return True


async def local_postgres_open(app) -> bool | None:
    """Whether PostgreSQL answers on this machine's loopback port, probed ONCE per process.

    The connect blocks, so it runs in a worker thread (:func:`asyncio.to_thread`) and never on the
    event loop; the answer is cached on ``app.state.setup_pg_probe``, so every later render reads it
    without touching a socket. The answer is tri-state — ``True`` (open), ``False`` (refused),
    ``None`` (no answer in time, or the probe could not run) — and only a proven ``True`` suppresses
    the install nudge, so an unknown port errs toward showing it.
    """
    if getattr(app.state, "setup_pg_probed", False):
        return cached_local_postgres(app)
    answer = await asyncio.to_thread(_probe_local_postgres, LOCAL_PROBE_TIMEOUT)
    app.state.setup_pg_probe = answer
    app.state.setup_pg_probed = True
    return answer


def cached_local_postgres(app) -> bool | None:
    """This process's cached probe answer, or ``None`` when it has not probed yet (→ show the nudge)."""
    if not getattr(app.state, "setup_pg_probed", False):
        return None
    answer = getattr(app.state, "setup_pg_probe", None)
    return answer if isinstance(answer, bool) else None


# ------------------------------------------------------------------------------- the table marks

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.I)
_VERSION_FILE_RE = re.compile(r"^update_v(\d+(?:\.\d+)+)\.sql$")
_recognised_cache: frozenset[str] | None = None
_schema_version_cache: str | None = None
_schema_version_loaded = False


def recognised_tables() -> frozenset[str]:
    """The table names DCSServerBot creates, derived from the repo's own SQL.

    Scanned from ``sql/tables.sql``, ``sql/cluster.sql`` and ``plugins/*/db/tables.sql`` — the same
    files the bot runs at boot — so a plugin that adds a table is recognised without an edit here.
    """
    global _recognised_cache
    if _recognised_cache is not None:
        return _recognised_cache
    names: set[str] = set()
    sources = [_REPO_ROOT / "sql" / "tables.sql", _REPO_ROOT / "sql" / "cluster.sql"]
    sources += sorted((_REPO_ROOT / "plugins").glob("*/db/tables.sql"))
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names.update(match.group(1).lower() for match in _TABLE_RE.finditer(text))
    _recognised_cache = frozenset(names)
    return _recognised_cache


def current_schema_version() -> str | None:
    """The newest schema version the shipped ``sql/update_v*.sql`` set can bring a database to."""
    global _schema_version_cache, _schema_version_loaded
    if _schema_version_loaded:
        return _schema_version_cache
    versions = []
    for path in (_REPO_ROOT / "sql").glob("update_v*.sql"):
        found = _VERSION_FILE_RE.match(path.name)
        if found:
            versions.append(found.group(1))
    _schema_version_cache = max(versions, key=parse) if versions else None
    _schema_version_loaded = True
    return _schema_version_cache


# ------------------------------------------------------------------------------- the outcomes

@dataclass
class TestOutcome:
    """One test's result.

    *code* is the classified state the tour turns into a sentence — never a driver string. *data*
    carries the values that sentence needs (a version, a table count, a table name, an attempt
    number). *ok* is whether the step's gate may pass. *version_outdated* is the amber
    Postgres-version warning, which rides on an outcome rather than blocking it.
    """

    code: str
    ok: bool = False
    version: str | None = None
    version_outdated: bool = False
    data: dict = field(default_factory=dict)


def _outcome(code: str, *, ok: bool = False, version: str | None = None,
             version_outdated: bool = False, **data) -> TestOutcome:
    return TestOutcome(code=code, ok=ok, version=version, version_outdated=version_outdated,
                       data=data)


class _ConnectFailure(Exception):
    """A failed connection: carries the classified outcome for :func:`run_test` to return."""

    def __init__(self, outcome: TestOutcome):
        super().__init__(outcome.code)
        self.outcome = outcome


# ------------------------------------------------------------------------------- the connect

def _connect(user: str, password: str, host: str, port: int, database: str):
    # connect_timeout bounds the wait: a firewalled host (SYN dropped, not refused) would otherwise
    # block for the full TCP timeout — the test runs synchronously inside the async route, so that
    # wait freezes the whole event loop, page and gate alike.
    return psycopg.connect(db_setup.connection_url(user, password, host, port, database),
                           autocommit=True, connect_timeout=CONNECT_TIMEOUT)


def _store_password(key: str, password: str, config_dir) -> None:
    """Write a password into ``config_dir/.secret/`` (the installer's primitive, dir ensured first).

    ``utils.set_password`` creates the secret directory of its DEFAULT config dir, so a tour whose
    config dir is elsewhere has to ensure the right one exists before the write.
    """
    utils.create_secret_dir(str(config_dir))
    utils.set_password(key, password, str(config_dir))


def _classify_connect_error(ex, *, path: str, host: str, port: int, user: str,
                            database: str) -> TestOutcome:
    """Turn a failed connection into one of the classified outcomes, in the operator's terms."""
    if isinstance(ex, psycopg.errors.InvalidPassword):
        if path == CREATE:
            return _outcome("master_password_refused", user=user)
        return _outcome("user_cannot_login", user=user, database=database)
    sqlstate = getattr(ex, "sqlstate", None)
    if sqlstate and sqlstate.startswith("3D"):               # invalid_catalog_name
        return _outcome("no_database", database=database)
    if isinstance(ex, psycopg.OperationalError):
        return _outcome("unreachable", host=host, port=port)
    return _outcome("connection_failed", host=host, port=port)


# ------------------------------------------------------------------------------- the inspector

def _table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'").fetchall()
    return {str(row[0]).lower() for row in rows}


def _schema_versions(conn) -> list[str]:
    try:
        rows = conn.execute("SELECT version FROM version").fetchall()
    except psycopg.Error:
        return []
    return [str(row[0]) for row in rows if row[0]]


def _is_older_schema(versions: list[str]) -> bool:
    current = current_schema_version()
    if not current or not versions:
        return False
    return all(parse(value) < parse(current) for value in versions)


def _is_empty(tables: set[str]) -> bool:
    return not tables


def _is_foreign(tables: set[str]) -> bool:
    """True when the database holds tables and NONE of them is one this bot creates."""
    known = recognised_tables()
    return bool(tables) and not (tables & known)


def _foreign_example(tables: set[str]) -> str:
    foreign = sorted(tables - recognised_tables())
    return foreign[0] if foreign else ""


# ------------------------------------------------------------------------------- path A (create)

def _create_master_connection(answers: DatabaseAnswers, master_password: str, config_dir):
    try:
        return _connect(answers.master_user, master_password, answers.host, answers.port,
                        answers.master_database)
    except psycopg.Error as ex:
        if isinstance(ex, psycopg.errors.InvalidPassword):
            pkl = Path(config_dir) / ".secret" / f"{master_secret_key(answers)}.pkl"
            if pkl.exists():
                pkl.unlink()
        raise _ConnectFailure(_classify_connect_error(
            ex, path=CREATE, host=answers.host, port=answers.port, user=answers.master_user,
            database=answers.master_database)) from ex


def _provision_create(answers: DatabaseAnswers, *, master_password: str, bot_password: str,
                      reset_user: bool, config_dir) -> TestOutcome:
    conn = _create_master_connection(answers, master_password, config_dir)
    with closing(conn):
        try:
            version = db_setup.get_server_version(conn)
            outdated = db_setup.is_version_outdated(version)
            existed_before = _database_exists(conn, answers.database)
            # install.py stores the master password the moment the connection succeeds (line 203);
            # the key is the block's own (see master_secret_key), so the two blocks never collide
            _store_password(master_secret_key(answers), master_password, config_dir)

            try:
                password = utils.get_password(answers.secret_key, config_dir) or ""
            except ValueError:
                password = ""
            password = password or secrets.token_urlsafe(8)

            created_user = False
            password_reset = False
            try:
                db_setup.create_user(conn, answers.user, password)
                created_user = True
            except psycopg.Error:
                # the role exists: keep its password if it verifies, otherwise ask, then reset
                if reset_user:
                    password = secrets.token_urlsafe(8)
                    db_setup.reset_user_password(conn, answers.user, password)
                    password_reset = True
                elif bot_password:
                    if db_setup.verify_login(answers.user, bot_password, answers.host,
                                             answers.port, answers.database):
                        password = bot_password
                    else:
                        attempts = answers.user_password_attempts + 1
                        if attempts >= MAX_USER_PASSWORD_ATTEMPTS:
                            password = secrets.token_urlsafe(8)
                            db_setup.reset_user_password(conn, answers.user, password)
                            password_reset = True
                        else:
                            return _outcome("user_password_refused", version=version,
                                            version_outdated=outdated, user=answers.user,
                                            attempts=attempts,
                                            left=MAX_USER_PASSWORD_ATTEMPTS - attempts)
                else:
                    return _outcome("user_exists", version=version, version_outdated=outdated,
                                    user=answers.user)

            _store_password(answers.secret_key, password, config_dir)
            created = db_setup.create_database(conn, answers.database, answers.user)

            if password_reset:
                code = "user_password_reset"
            elif created_user:
                code = "ok_created"
            else:
                code = "ok_reused_user"
            if not created:
                if not existed_before:
                    # the database did not exist before the attempt, so create_database's swallowed
                    # error is a refused CREATE or GRANT/ALTER — never "the database already existed".
                    return _outcome("provision_failed", version=version,
                                    version_outdated=outdated, database=answers.database,
                                    user=answers.user)
                # the database existed: say what is in it instead of the plain "already existed",
                # and never report a success when the bot's own login cannot be proven.
                refined = _refine_existing_database(answers, password)
                if refined is not None:
                    refined.version = version
                    refined.version_outdated = outdated
                    return refined
                code = "ok_reused_database"
            return _outcome(code, ok=not code.startswith("bad_"), version=version,
                            version_outdated=outdated, database=answers.database, user=answers.user,
                            password_reset=password_reset)
        except psycopg.Error as ex:
            # ANY driver error past the master connection — a denied reset, a connection dropped
            # mid-DDL — is one classified outcome, never a bare 500 and never a green light.
            log.warning("provisioning %s failed: %s", answers.database, ex)
            return _outcome("provision_failed", database=answers.database, user=answers.user)


def _database_exists(conn, database: str) -> bool:
    """True when the target database already exists on the server (the master connection's view)."""
    row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
    return row is not None


def _refine_existing_database(answers: DatabaseAnswers, password: str) -> TestOutcome | None:
    """Classify an already-existing target database as the bot's own user.

    Returns the classified outcome for a database that holds data, a FAILURE outcome when the bot's
    own login cannot be established, or ``None`` when the database is empty (so the caller reports
    the plain "the database already existed" success).
    """
    try:
        conn = _connect(answers.user, password, answers.host, answers.port, answers.database)
    except psycopg.Error as ex:
        # the bot's own login was never proven — classify the driver error like the existing path
        # does, so the gate stays closed instead of claiming the database was reused.
        return _classify_connect_error(ex, path=EXISTING, host=answers.host, port=answers.port,
                                       user=answers.user, database=answers.database)
    with closing(conn):
        tables = _table_names(conn)
        if _is_empty(tables):
            return None
        if _is_foreign(tables):
            return _outcome("bad_foreign_tables", database=answers.database, count=len(tables),
                            example=_foreign_example(tables))
        return _outcome("bad_database_holds_data", database=answers.database)


# ------------------------------------------------------------------------------- path B (existing)

def _probe_existing(answers: DatabaseAnswers, *, bot_password: str) -> TestOutcome:
    try:
        conn = _connect(answers.user, bot_password, answers.host, answers.port, answers.database)
    except psycopg.Error as ex:
        raise _ConnectFailure(_classify_connect_error(
            ex, path=EXISTING, host=answers.host, port=answers.port, user=answers.user,
            database=answers.database)) from ex

    with closing(conn):
        version = db_setup.get_server_version(conn)
        outdated = db_setup.is_version_outdated(version)
        tables = _table_names(conn)
        if _is_empty(tables):
            return _outcome("ok_existing_empty", ok=True, version=version,
                            version_outdated=outdated, database=answers.database)
        if _is_foreign(tables):
            return _outcome("bad_foreign_tables", version=version, version_outdated=outdated,
                            database=answers.database, count=len(tables),
                            example=_foreign_example(tables))
        if _is_older_schema(_schema_versions(conn)):
            return _outcome("ok_existing_older", ok=True, version=version,
                            version_outdated=outdated, database=answers.database,
                            count=len(tables))
        return _outcome("ok_existing_data", ok=True, version=version, version_outdated=outdated,
                        database=answers.database, count=len(tables))


# ------------------------------------------------------------------------------- the entry point

def run_test(answers: DatabaseAnswers, *, master_password: str, bot_password: str,
             reset_user: bool = False, config_dir) -> TestOutcome:
    """Run step 3's test for the chosen path and return the classified outcome.

    ``CREATE`` writes (the installer's DDL) and needs ``master_password``; ``EXISTING`` is
    read-only and needs the bot user's ``bot_password``. ``reset_user`` is the create path's
    explicit "reset the existing role's password" acknowledgement. Which secret file the create path
    writes is ``answers.secret_key`` — ``database`` for the local block, ``clusterdb`` for the
    cluster block.

    The create path's master user is validated BEFORE any connection: it becomes both a URL userinfo
    component and a ``config/.secret/<key>.pkl`` file name, so an unsafe name (a ``/``, a ``.``) or a
    reserved secret key is refused here rather than written to the secret store — a distinct
    ``bad_reserved_master_user`` outcome, so the operator is told why.
    """
    if answers.path == CREATE:
        if db_setup.is_reserved_master_user(answers.master_user):
            return _outcome("bad_reserved_master_user", user=answers.master_user)
        if not db_setup.is_valid_master_user(answers.master_user):
            return _outcome("bad_master_user", user=answers.master_user)
    try:
        if answers.path == CREATE:
            return _provision_create(answers, master_password=master_password,
                                     bot_password=bot_password, reset_user=reset_user,
                                     config_dir=config_dir)
        return _probe_existing(answers, bot_password=bot_password)
    except _ConnectFailure as failure:
        return failure.outcome
