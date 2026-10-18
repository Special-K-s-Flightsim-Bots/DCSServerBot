"""Step 9 of the tour — Review and write: the real configuration, in one ordered commit.

WHAT THIS MODULE OWNS. The answers live in the tour's draft (:mod:`services.webservice.setup.state`)
and in the two process-memory secret stores (the database passwords and the Discord token). This
module turns them into the files the bot and the console read, and writes them through the ONE
config-write primitive (:func:`core.utils.configwrite.save_config_file`) with the bot's own schemas
(:func:`services.webservice.configgen.schema_files_for`) — so a tour write is validated, atomic and
`.bak`-ed exactly as a Settings-page save is.

MAIN.YAML IS WRITTEN LAST, AND THAT IS THE CLOSING RULE. The setup window exists iff ``main.yaml``
is absent when the process starts (:mod:`services.webservice.setup.boot`). Every other target here
is a configuration file the bot reads; ``main.yaml`` is the one file that means "configured". Written
first it would close the window mid-commit; written last, a refusal anywhere else leaves the
installation unconfigured and the tour runnable — which is why a commit that did not write
``main.yaml`` is NOT a finished setup and :class:`WriteReport` says so rather than reporting success.

THE SECRETS GO TO THE INSTALLER'S OWN STORE, NOT TO A CONFIG FILE. ``config/.secret/<key>.pkl``
through :func:`core.utils.os.set_password`: the Discord bot token (``token``) and — on the existing
database path, where the test is read-only and stores nothing — the bot's database password
(``database``), plus the CLUSTER database's password (``clusterdb``) when step 3's optional
Federation support block is ticked and verifies. The connection URL written into ``nodes.yaml``
carries the literal ``SECRET`` placeholder, which the bot substitutes from that store at boot
(``core.data.impl.nodeimpl.get_database_urls``), so no password ever reaches a config file.

WHAT IS NOT DONE HERE. No download, no install, no ``MissionScripting.lua`` edit, and no write
outside the config directory. The command line installer's SRS ``Config/SRS.cfg`` copy is
deliberately not reproduced: it writes into ``Saved Games``, outside the config directory, which the
setup console's own security model forbids (see :mod:`services.webservice.setup.app`). The SRS
installation path and each instance's derived port are still written to ``nodes.yaml``.
"""

from __future__ import annotations

import logging
import os
import platform

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import utils
from core.utils import configwrite, db_setup, setup_config

from .. import configgen
from . import community as community_step
from . import console as console_step
from . import database as db_step
from . import discord as discord_step
from . import join as join_step
from . import node as node_step
from . import servers as servers_step
from .boot import INSTALL, JOIN
from .state import SetupDraft

__all__ = [
    "REPO_ROOT",
    "CONFIG_PREFIX",
    "DEFAULT_CONSOLE_PORT",
    "Target",
    "MAIN",
    "BOT",
    "NODES",
    "SERVERS",
    "SCHEDULER",
    "CLOUD",
    "WEBSERVICE",
    "TOKEN_SECRET",
    "DATABASE_SECRET",
    "CLUSTER_SECRET",
    "DISCORD_CLIENT_SECRET",
    "FILE_TARGETS",
    "SECRET_TARGETS",
    "TARGETS",
    "SECRET_STEPS",
    "JOIN_MERGE_TARGETS",
    "JOIN_FORBIDDEN_TARGETS",
    "JoinCollisionError",
    "JoinNodeDatabaseError",
    "join_database_refusal",
    "PlannedWrite",
    "WriteOutcome",
    "WriteReport",
    "node_name",
    "federation_on",
    "cluster_answers",
    "cluster_written",
    "required_secrets",
    "missing_secrets",
    "plan",
    "write",
]

log = logging.getLogger(__name__)

#: The repository root, four directories up from this module — where the shipped schemas live.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The prefix the Review screen shows in front of a target's relative path.
CONFIG_PREFIX = "config"

#: The console's port when the application did not record the one it served on — the stable port the
#: admin console uses, so the URL the operator already has keeps working.
DEFAULT_CONSOLE_PORT = 9876


@dataclass(frozen=True)
class Target:
    """One file (or one secret) the Review step writes.

    ``key`` is the stable id the screen and the report are keyed on, ``relpath`` is the path under
    the config directory (POSIX, for display and for the file targets), and ``schema`` is the
    repo-relative schema file the bot itself validates that file against — ``None`` for a secret,
    which is a pickle in the installer's own store rather than a validated YAML document.
    """

    key: str
    relpath: str
    schema: str | None


#: The Discord bot token, in the installer's secret store (``config/.secret/token.pkl``).
TOKEN_SECRET = Target(key="token", relpath=".secret/token.pkl", schema=None)

#: The bot's database password, written only on the existing-database path (see the module docstring).
DATABASE_SECRET = Target(key="database_password", relpath=".secret/database.pkl", schema=None)

#: The CLUSTER database's password — the optional federation block's credential. Written only when
#: step 3's Federation support box is ticked and the cluster block's own Test verifies, into the key
#: the bot reads for the cluster pool (``nodeimpl.get_database_urls`` → ``.secret/clusterdb.pkl``,
#: ``config/main.yaml``'s ``database`` block). Never ``database.pkl``: that is the node's OWN password.
CLUSTER_SECRET = Target(key="clusterdb_password", relpath=".secret/clusterdb.pkl", schema=None)

#: The console's Discord client secret, in the installer's secret store. Written only when the Web
#: console is enabled AND the Discord login is configured, into the exact key the OAuth backend reads
#: (``auth.discord.client_secret_key``).
DISCORD_CLIENT_SECRET = Target(key="discord_client_secret",
                               relpath=".secret/discord_client_secret.pkl", schema=None)

#: The configuration files, in the order they are written. ``main.yaml`` is NOT here on purpose:
#: it is written last, after every file below it succeeded — see :func:`plan`.
#:
#: TWO TARGETS CARRY NO SCHEMA, AND THAT IS DELIBERATE — the bot cannot validate either file before
#: a first-time setup has written it:
#:
#: * ``nodes.yaml`` (``schemas/nodes_schema.yaml``): the schema requires a per-instance
#:   ``webgui_port`` and ``dcs_port`` and checks that the DCS installation and each instance home
#:   exist. The installer's own instance shape — and therefore this one, which mirrors it — carries
#:   neither key: the bot derives them and rewrites the file on the first boot
#:   (``core/data/impl/nodeimpl.py``). A schema check here would refuse the configuration the command
#:   line installer has always written.
#: * ``plugins/scheduler.yaml`` (``plugins/scheduler/schemas/scheduler_schema.yaml``): its
#:   ``check_main_structure`` reads the install's EXISTING ``nodes.yaml`` and ``servers.yaml`` through
#:   ``core/utils/validators.py``'s ``NodeData``, which on a first-time setup do not exist yet.
BOT = Target(key="bot", relpath="services/bot.yaml",
             schema="services/bot/schemas/bot_schema.yaml")
NODES = Target(key="nodes", relpath="nodes.yaml", schema=None)
SERVERS = Target(key="servers", relpath="servers.yaml", schema="schemas/servers_schema.yaml")
SCHEDULER = Target(key="scheduler", relpath="plugins/scheduler.yaml", schema=None)
CLOUD = Target(key="cloud", relpath="plugins/cloud.yaml",
               schema="plugins/cloud/schemas/cloud_schema.yaml")
WEBSERVICE = Target(key="webservice", relpath="services/webservice.yaml",
                    schema="services/webservice/schemas/webservice_schema.yaml")

#: The one file that closes the setup window. Always the last write of a commit.
MAIN = Target(key="main", relpath="main.yaml", schema="schemas/main_schema.yaml")

#: The optional configuration files, in write order (an absent one is simply not written).
FILE_TARGETS = (BOT, NODES, SERVERS, SCHEDULER, CLOUD, WEBSERVICE)

#: Every target, in the order the Review screen lists them (main.yaml last).
SECRET_TARGETS = (TOKEN_SECRET, DISCORD_CLIENT_SECRET, DATABASE_SECRET, CLUSTER_SECRET)
TARGETS = SECRET_TARGETS + FILE_TARGETS + (MAIN,)

#: The tour step each secret is entered on — the step the operator returns to when the value is not
#: held in this process. The Discord token is step 5's, the two database passwords step 3's.
SECRET_STEPS: dict[str, int] = {TOKEN_SECRET.key: 5, DATABASE_SECRET.key: 3,
                                CLUSTER_SECRET.key: 3}

#: The targets a JOINING commit may reach ONLY through the merge. ``nodes.yaml`` is keyed by node
#: name, ``servers.yaml`` by server name and ``plugins/scheduler.yaml`` by instance name — all three
#: are CLUSTER-WIDE documents, so writing one whole would drop every other node's rows.
JOIN_MERGE_TARGETS = (NODES, SERVERS, SCHEDULER)

#: The targets a JOINING commit must NEVER write. This installation is already configured: its
#: ``main.yaml``, its guild role mapping, its console (account and OAuth client), its DGSA opt-ins and
#: its Discord token are the install's, not this node's, and none of them is this flow's to change.
JOIN_FORBIDDEN_TARGETS = (MAIN, BOT, WEBSERVICE, CLOUD, TOKEN_SECRET, DISCORD_CLIENT_SECRET,
                          CLUSTER_SECRET)


class JoinCollisionError(configwrite.ConfigWriteError):
    """A joining merge refused: a key this node would write is another node's row already.

    Raised from inside the merge's mutation, under the file's own write lock, so the refusal and the
    read that found it cannot be raced. ``key`` names the colliding top-level key (a node, server or
    instance name) and ``path`` the shared document it lives in.
    """

    def __init__(self, message: str, *, path, key: str):
        super().__init__(message, path=path)
        self.key = key


class JoinNodeDatabaseError(configwrite.ConfigWriteError):
    """A joining commit refused: a FEDERATED install's node has no database of its own to carry.

    ``main.yaml``'s ``database`` is the CLUSTER database on a federated install
    (``core.data.impl.nodeimpl.get_database_urls``). Writing this node's block with no ``database.url``
    would make the node inherit the federation — a wrong database written into a running cluster. The
    message names the missing piece so the operator knows which answer to give.

    Raised while the plan is built, so nothing reaches the disk.
    """

    def __init__(self, message: str):
        super().__init__(message)


@dataclass
class PlannedWrite:
    """One thing a commit will do: a validated file write, a secret-store write, or a merge.

    ``merge`` selects the write: ``False`` (the default, and every first-run write) replaces the whole
    document through :func:`core.utils.configwrite.save_config_file`; ``True`` merges the document's
    top-level keys into the file's own round-trip document through
    :func:`core.utils.configwrite.patch_config_file`, leaving every other key as the file holds it.
    """

    target: Target
    document: Any = None
    #: ``(key, value)`` for a secret write, ``None`` for a file write
    secret: tuple[str, str] | None = None
    #: whether the write merges into the existing document instead of replacing it
    merge: bool = False


@dataclass
class WriteOutcome:
    """What happened to one target.

    ``ok`` is whether it reached the disk, ``changed`` whether the write actually altered anything
    (the primitive's no-op rule answers ``False`` for a file already in the shape we would write),
    ``skipped`` whether it was never attempted because an earlier file was refused, and ``error``
    the operator-facing reason a failed write names.
    """

    target: Target
    ok: bool = False
    changed: bool = False
    skipped: bool = False
    error: str = ""


@dataclass
class WriteReport:
    """The whole commit's result, one outcome per target, in write order.

    ``main_written`` is the first-run closing rule as a fact: the commit is finished iff ``main.yaml``
    reached the disk. A report with ``main_written`` false describes a HALF-WRITTEN configuration and
    the screen says exactly which files did and did not land.

    ``completed`` is the same question asked of the mode's OWN closing target (``closing``): in a
    first-run commit that is ``main.yaml``, in a joining commit the node's ``nodes.yaml`` merge. The
    two are deliberately separate facts — a joining commit never writes ``main.yaml``, so overloading
    ``main_written`` would report a finished setup for a node that never landed.
    """

    outcomes: list[WriteOutcome] = field(default_factory=list)
    #: the target whose write makes THIS commit finished
    closing: Target = MAIN

    @property
    def main_written(self) -> bool:
        return any(outcome.target is MAIN and outcome.ok for outcome in self.outcomes)

    @property
    def completed(self) -> bool:
        """Whether the mode's closing target reached the disk (see the class docstring)."""
        return any(outcome.target is self.closing and outcome.ok for outcome in self.outcomes)

    @property
    def written(self) -> list[WriteOutcome]:
        return [outcome for outcome in self.outcomes if outcome.ok]

    @property
    def failed(self) -> list[WriteOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.ok]


def node_name(app=None) -> str:
    """The node name ``nodes.yaml`` is keyed on.

    The launcher's own name, recorded on the application at start (``webui_setup_node``); the
    hostname is the fallback, which is what the launcher passes by default anyway.
    """
    recorded = str(getattr(getattr(app, "state", None), "webui_setup_node", "") or "").strip()
    return recorded or platform.node()


# -------------------------------------------------------------------------- the cluster block

def federation_on(draft: SetupDraft) -> bool:
    """Whether step 3's ``Federation support`` box is ticked (absent in the draft means OFF)."""
    return bool(draft.database.get(db_step.FEDERATION_KEY))


def cluster_answers(draft: SetupDraft) -> db_step.DatabaseAnswers:
    """The cluster block's answers (the installer's defaults when the block is absent)."""
    return db_step.answers_from_mapping(draft.database.get(db_step.CLUSTER_KEY),
                                        secret_key=db_step.CLUSTER_SECRET_KEY)


def cluster_written(draft: SetupDraft, *, app=None) -> bool:
    """Whether the cluster database block reaches ``main.yaml``.

    Both conditions of the write gate: the box is ticked AND the block's own Test still verifies (its
    fingerprint matches the current values and the password this process holds) — the same rule the
    local block's Continue applies, applied to the cluster block.
    """
    return federation_on(draft) and db_step.verification(
        draft.database.get(db_step.CLUSTER_KEY), secret_key=db_step.CLUSTER_SECRET_KEY, app=app)


# ------------------------------------------------------------------------------- the documents

def _database_url(answers: db_step.DatabaseAnswers) -> str:
    """The connection URL stored in ``nodes.yaml``: the password is the literal ``SECRET``.

    The bot substitutes it from ``config/.secret/database.pkl`` at boot, so the same URL shape serves
    both database paths (the installer stores exactly this one).
    """
    return db_setup.config_connection_url(answers.user, answers.host, int(answers.port),
                                          answers.database)


def _node_document(draft: SetupDraft, app=None, *, include_database: bool = True) -> dict:
    """The whole ``nodes.yaml`` document: this node, its database URL, DCS and its instances.

    NO ``extensions`` block and NO per-instance SRS keys: extensions leave the tour (their home is the
    console's node page), so the tour writes only what brings the servers up — the node, and one
    ``instances`` entry per managed instance with its ``home`` and ``bot_port``.

    THIS IS A WHOLE DOCUMENT, AND THAT IS ONLY EVER CORRECT ON A FIRST RUN. On a joining install it
    would drop every other node's block; the joining plan merges the same key instead
    (:func:`_join_plan`).
    """
    return {node_name(app): _node_block(draft, app, include_database=include_database)}


def _node_block(draft: SetupDraft, app=None, *, include_database: bool = True) -> dict:
    """This node's own ``nodes.yaml`` block, without the key it is stored under.

    *include_database* is the caller's rule for the node-level ``database`` block. On a non-federated
    install it follows the CLI (``install.py``: ``database_url if 'database' not in main else None``): a
    node whose install already carries the shared database in ``main.yaml`` INHERITS it and writes no
    block of its own. On a federated install ``main.yaml``'s database is the CLUSTER database, so the
    block is always written (:func:`_join_plan`).
    """
    node_answers = node_step.answers_from_mapping(draft.node)
    servers_answers = servers_step.answers_from_mapping(draft.servers)
    db_answers = db_step.answers_from_mapping(draft.database)

    node = setup_config.build_node(
        listen_port=int(node_answers.listen_port),
        use_upnp=bool(node_answers.use_upnp),
        public_ip=node_answers.public_ip,
        database_url=_database_url(db_answers) if include_database else None,
        dcs_installation=node_answers.dcs_path,
        dcs_autoupdate=bool(node_answers.dcs_autoupdate),
    )
    instances = servers_step.instance_blocks(servers_answers, base=_bot_port_base(app))
    if instances:
        node["instances"] = instances
    return node


def _bot_port_base(app=None) -> int:
    """The first bot port this commit may assign, given the ``nodes.yaml`` already on disk.

    Read from the installation's own config directory, because the port the commit writes must be the
    port the step RENDERED (``setup.tour._bot_port_base`` reads the same file the same way). An
    application that records no directory, or a file that is absent or unreadable, answers the
    installer's own first port — which is what a first-time setup gets.
    """
    config_dir = _config_dir_of(app)
    if not config_dir:
        return servers_step.DEFAULT_BOT_PORT
    path = Path(config_dir) / "nodes.yaml"
    try:
        nodes = configwrite.load_config_file(path)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: '%s' could not be read (%s); assigning bot ports from the "
                    "installer's own first port.", path, ex)
        return servers_step.DEFAULT_BOT_PORT
    return servers_step.bot_port_base(nodes)


def _bot_document(draft: SetupDraft) -> dict:
    """``services/bot.yaml``: the Discord role mapping, or the no-Discord community one."""
    if draft.discord:
        return discord_step.bot_block(discord_step.answers_from_mapping(draft.discord_bot),
                                      discord_step.roles_from_mapping(draft.roles))
    return community_step.bot_block(community_step.answers_from_mapping(draft.community))


def _servers_document(draft: SetupDraft) -> dict:
    """``servers.yaml``: one channels block per managed server (empty on the no-Discord path)."""
    servers_answers = servers_step.answers_from_mapping(draft.servers)
    central_admin = (discord_step.roles_from_mapping(draft.roles).central_admin
                     if draft.discord else False)
    return servers_step.servers_entries(servers_answers, discord=draft.discord,
                                        central_admin=central_admin)


def _scheduler_document(draft: SetupDraft) -> dict:
    """``plugins/scheduler.yaml``: one autostart entry per managed instance."""
    return servers_step.scheduler_entries(servers_step.answers_from_mapping(draft.servers))


def _cloud_document(draft: SetupDraft) -> dict | None:
    """``plugins/cloud.yaml``: the shipped sample with the two DGSA opt-ins set, or ``None``.

    Seeded from ``samples/plugins/cloud.yaml`` exactly as the installer does, so the cloud plugin's
    host/port/protocol defaults survive the two opt-ins this step owns.
    """
    opt_ins = node_step.cloud_block(node_step.answers_from_mapping(draft.node))
    if opt_ins is None:
        return None
    document: Any = {}
    sample = REPO_ROOT / "samples" / "plugins" / "cloud.yaml"
    if sample.is_file():
        try:
            document = configwrite.load_config_file(sample) or {}
        except configwrite.ConfigWriteError as ex:
            log.warning("Setup tour: the cloud sample at '%s' could not be read (%s); writing the "
                        "two opt-ins only.", sample, ex)
            document = {}
    default = document.setdefault("DEFAULT", {})
    default["dcs-ban"] = bool(opt_ins["dcs"])
    default["discord-ban"] = bool(opt_ins["discord"])
    return document


def _webservice_document(draft: SetupDraft, app=None) -> dict:
    """``services/webservice.yaml``: the console itself, the doors that open it and the Discord login.

    ENABLED. The listener is the loopback interface the setup console already uses, so the first
    console the tour hands over is reachable exactly where the tour was. The identity block carries the
    **break-glass account** the tour collected — ALWAYS, never a "Discord only" console — and, when the
    bot is a Discord bot and a client secret is held, the ``auth.discord`` block whose
    ``client_secret_key`` is the store key the secret was written under.

    DISABLED. A minimal document is still written (``frontend: false`` plus the listener and the
    address) so enabling the console later is ONE key, and no account is invented: nothing is asked on
    a console-less install.
    """
    port = console_port(app)
    answers = console_step.answers_from_mapping(draft.console)
    public_base_url = answers.public_base_url or f"http://localhost:{port}"
    if not answers.enabled:
        return {"DEFAULT": {"listen": "127.0.0.1", "port": port, "frontend": False,
                            "auth": {"public_base_url": public_base_url}}}
    auth: dict = {"public_base_url": public_base_url}
    token = discord_step.token_store(app).get(discord_step.TOKEN_KEY)
    client_id = discord_step.application_id(token) if draft.discord else None
    if client_id:
        secret_held = bool(discord_step.client_secret_store(app).get(discord_step.CLIENT_SECRET_KEY))
        discord_block = console_step.discord_block(answers, client_id, secret_held=secret_held)
        if discord_block:
            auth["discord"] = discord_block
    account = console_step.account_block(answers)
    if account:
        auth["breakglass"] = account
    return {"DEFAULT": {"listen": "127.0.0.1", "port": port, "frontend": True, "auth": auth}}


def console_port(app=None) -> int:
    """The console's port: the one the setup console served on, so the documented URL keeps working.

    Read from the application (``webui_setup_port``, recorded by the service at start) with the
    stable default as the fallback, so this module depends on no service import.
    """
    recorded = getattr(getattr(app, "state", None), "webui_setup_port", None)
    if recorded in (None, ""):
        return DEFAULT_CONSOLE_PORT
    try:
        return int(recorded)
    except (TypeError, ValueError):
        return DEFAULT_CONSOLE_PORT


def _main_document(draft: SetupDraft, app=None) -> dict:
    """``main.yaml`` — the last write, and the one that closes the setup window.

    The OPTIONAL cluster database block is added here, and only when the write gate holds
    (:func:`cluster_written`): the box is ticked and the cluster block's own Test verifies. It
    carries the ``database`` map the bot reads as its cluster pool's URL — ``url`` alone, no pool keys
    and no SSL choice, which the bot's own defaults fill in. With the box off the document is exactly
    today's: no ``database`` key at all.
    """
    if draft.discord:
        bot_answers = discord_step.answers_from_mapping(draft.discord_bot)
        assert bot_answers.guild_id is not None   # a completed step 6 always carries one
        document = setup_config.build_main(int(bot_answers.guild_id), bool(draft.autoupdate),
                                           draft.language if draft.bot_language else None)
    else:
        community_answers = community_step.answers_from_mapping(draft.community)
        document = setup_config.build_no_discord_main(community_answers.guild_name,
                                                      bool(draft.autoupdate))
    if cluster_written(draft, app=app):
        cluster = cluster_answers(draft)
        document["database"] = {"url": db_setup.config_connection_url(
            cluster.user, cluster.host, int(cluster.port), cluster.database)}
    return document


# ------------------------------------------------------------------------------------ the plan

def _secret_held(target: Target, *, app=None) -> bool:
    """Whether *target*'s value is held by THIS process's own store (never read from disk)."""
    if target is TOKEN_SECRET:
        return bool(discord_step.token_store(app).get(discord_step.TOKEN_KEY))
    if target is DATABASE_SECRET:
        return bool(db_step.secret_store(app).get(db_step.BOT_PASSWORD_KEY))
    if target is CLUSTER_SECRET:
        return bool(db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)
                    .get(db_step.BOT_PASSWORD_KEY))
    return True


def required_secrets(draft: SetupDraft, *, mode: str = INSTALL) -> tuple[Target, ...]:
    """The secret targets a commit MUST hold in this process, in write order.

    FIRST RUN. The Discord bot token whenever Discord support is on; the bot's database password on
    the existing-database path — whose test is read-only and writes nothing, so the password reaches
    ``config/.secret/database.pkl`` only at the commit; and the CLUSTER database's password on the
    same terms while Federation support is ticked. The create path's passwords are NOT required here:
    each block's test already wrote its secret to disk (``database.pkl`` / ``clusterdb.pkl``), so it
    survives a restart without this process holding it.

    JOINING. This install already carries its token and its cluster database, and the joining flow
    never writes either — so the token is NOT required and only the local database password is, on the
    same existing-path terms. Requiring the token would refuse to commit on an install whose token the
    flow has no business asking for.
    """
    if mode == JOIN:
        if db_step.answers_from_mapping(draft.database).is_create:
            return ()
        return (DATABASE_SECRET,)
    required: list[Target] = []
    if draft.discord:
        required.append(TOKEN_SECRET)
    if not db_step.answers_from_mapping(draft.database).is_create:
        required.append(DATABASE_SECRET)
    if federation_on(draft) and not cluster_answers(draft).is_create:
        required.append(CLUSTER_SECRET)
    return tuple(required)


def missing_secrets(draft: SetupDraft, *, app=None, mode: str = INSTALL) -> list[Target]:
    """The REQUIRED secret targets this process does not hold — what a mid-tour restart dropped.

    A non-empty answer means the Review step must refuse to render its commit or write anything: a
    commit now would write the closing file and leave a configuration the bot cannot sign in with (no
    token) or reach its database with (no password). The value lives in process memory only, so the
    caller names the step to re-enter (:data:`SECRET_STEPS`).
    """
    return [target for target in required_secrets(draft, mode=mode)
            if not _secret_held(target, app=app)]


def plan(draft: SetupDraft, *, app=None, mode: str = INSTALL) -> list[PlannedWrite]:
    """Everything a commit will do, in the order it happens: secrets, files, then the closing file.

    Only targets that are actually wanted appear, so the Review screen's "will be written" list is
    the commit itself rather than a promise. A target that would be empty is left out exactly as the
    installer leaves it out (an empty scheduler mapping, a cloud block nobody opted into, a secret
    that is not held).

    JOINING is a different list AND a different kind of write (:func:`_join_plan`): three merges into
    documents that are already there, and nothing install-wide.
    """
    if mode == JOIN:
        return _join_plan(draft, app=app)
    writes: list[PlannedWrite] = []

    if draft.discord:
        token = discord_step.token_store(app).get(discord_step.TOKEN_KEY)
        if token:
            writes.append(PlannedWrite(target=TOKEN_SECRET, secret=("token", token)))
        # the console's Discord login: the secret goes to the store KEY its block names, and only
        # when the console is on and the application id (derived from the token) is known
        console_answers = console_step.answers_from_mapping(draft.console)
        client_secret = discord_step.client_secret_store(app).get(discord_step.CLIENT_SECRET_KEY)
        if client_secret and console_answers.enabled and discord_step.application_id(token):
            writes.append(PlannedWrite(target=DISCORD_CLIENT_SECRET,
                                       secret=(discord_step.CLIENT_SECRET_KEY, client_secret)))
    # the existing-database path's test is read-only, so its password reaches the store only here;
    # the create path's test already wrote config/.secret/database.pkl (the installer's own order)
    if not db_step.answers_from_mapping(draft.database).is_create:
        password = db_step.secret_store(app).get(db_step.BOT_PASSWORD_KEY)
        if password:
            writes.append(PlannedWrite(target=DATABASE_SECRET, secret=("database", password)))
    # the optional cluster block, on the same terms: written only while the box is ticked AND its own
    # Test verifies. Its create path's test already wrote config/.secret/clusterdb.pkl; the existing
    # path's read-only test did not, so the password reaches the store only here. Secrets write BEFORE
    # the files, so a refusal later in the plan can leave this file on disk with no main.yaml naming it
    # — inert, and the installer's own order. It is deliberately NOT moved after main.yaml: that write
    # closes the setup window, and a password that fails after it would leave a configuration that
    # cannot sign in.
    if cluster_written(draft, app=app):
        cluster = cluster_answers(draft)
        if not cluster.is_create:
            password = db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY).get(
                db_step.BOT_PASSWORD_KEY)
            if password:
                writes.append(PlannedWrite(target=CLUSTER_SECRET,
                                           secret=(db_step.CLUSTER_SECRET_KEY, password)))

    writes.append(PlannedWrite(target=BOT, document=_bot_document(draft)))
    writes.append(PlannedWrite(target=NODES, document=_node_document(draft, app)))
    writes.append(PlannedWrite(target=SERVERS, document=_servers_document(draft)))
    scheduler = _scheduler_document(draft)
    if scheduler:
        writes.append(PlannedWrite(target=SCHEDULER, document=scheduler))
    cloud = _cloud_document(draft)
    if cloud is not None:
        writes.append(PlannedWrite(target=CLOUD, document=cloud))
    writes.append(PlannedWrite(target=WEBSERVICE, document=_webservice_document(draft, app)))
    writes.append(PlannedWrite(target=MAIN, document=_main_document(draft, app)))
    return writes


# ----------------------------------------------------------------------------------- the write

def _schema_files(target: Target) -> list[str] | None:
    """The schema files the bot validates *target* against, or ``None`` for a secret."""
    if not target.schema:
        return None
    return configgen.schema_files_for(REPO_ROOT / target.schema)


def _join_plan(draft: SetupDraft, *, app=None) -> list[PlannedWrite]:
    """What a JOINING commit will do: this node's servers, its autostart entries, then the node.

    THREE MERGES AND ONE OPTIONAL SECRET, AND NOTHING ELSE. Every target here is merged into a
    document that already exists and that other nodes share, so the document's parser — not this
    module — is what preserves the other nodes' blocks, their comments and their key order. The list
    contains no ``main.yaml``, no ``services/bot.yaml``, no ``services/webservice.yaml``, no
    ``plugins/cloud.yaml`` and no install token (:data:`JOIN_FORBIDDEN_TARGETS`); the write funnel
    refuses any of those even if a later edit of this function asks for one.

    ORDER: servers and their autostart entries first, the NODE LAST. The node's merge is what closes
    the setup window for this node (:func:`services.webservice.setup.boot.setup_mode`), so writing it
    last means a refused ``servers.yaml`` leaves the node out of ``nodes.yaml`` and the flow
    repeatable, instead of adding a node whose servers never landed.

    The three Discord channel fields ARE asked in this mode, from the EXISTING installation's own
    Discord context (see :func:`setup.join.discord_context`): its token and guild id feed the pickers
    and its ``no_discord``/central-admin answers decide the shape, exactly as step 2 and step 6 do on
    a first run — so a managed server is written with the channels the operator picked, and a
    no-Discord installation still writes the installer's empty shape.

    THE NODE'S DATABASE BLOCK FOLLOWS THE CLI ON A NON-FEDERATED INSTALL, AND ONLY THERE. With no
    federation, ``main.yaml``'s database IS the shared one and a node whose install already carries it
    inherits it and writes no ``database`` block of its own — the same rule ``install.py`` applies
    (``database_url if 'database' not in main else None``). A FEDERATED install is the opposite case:
    there ``main.yaml``'s ``database`` is the CLUSTER database, so this node MUST carry its OWN URL
    from the database step (:func:`_require_join_node_database`), and a step that produced none refuses
    the whole commit rather than writing a block that would inherit the federation.
    """
    federated = _federated(app)
    if federated:
        _require_join_node_database(draft)
    writes: list[PlannedWrite] = []
    # the existing-database path's test is read-only, so its password reaches the store only here
    if not db_step.answers_from_mapping(draft.database).is_create:
        password = db_step.secret_store(app).get(db_step.BOT_PASSWORD_KEY)
        if password:
            writes.append(PlannedWrite(target=DATABASE_SECRET, secret=("database", password)))
    servers_answers = servers_step.answers_from_mapping(draft.servers)
    # the joining install's own Discord context decides the channel line, exactly as step 2/step 6 do
    # on a first run (see :func:`setup.join.discord_context`)
    discord = join_step.discord_context(_config_dir_of(app))
    writes.append(PlannedWrite(target=SERVERS,
                               document=servers_step.servers_entries(
                                   servers_answers, discord=discord.discord,
                                   central_admin=discord.central_admin),
                               merge=True))
    scheduler = servers_step.scheduler_entries(servers_answers)
    if scheduler:
        writes.append(PlannedWrite(target=SCHEDULER, document=scheduler, merge=True))
    writes.append(PlannedWrite(target=NODES,
                               document=_node_document(draft, app,
                                                       include_database=federated
                                                       or not _cluster_owns_database(app)),
                               merge=True))
    return writes


def _config_dir_of(app=None):
    """The config directory the setup application records on its state, or ``None``."""
    return getattr(getattr(app, "state", None), "webui_config_dir", None)


def _federated(app=None) -> bool:
    """Whether this installation is federated, from the bot's OWN signal (:func:`database.is_federated`).

    The signal is ``config/.secret/clusterdb.pkl`` — exactly what the database step's pre-fill asks
    (:func:`services.webservice.setup.database.cluster_database_url`), so this commit and the pre-fill
    can never disagree about which database ``main.yaml`` names. An application that records no config
    directory is not federated.
    """
    config_dir = getattr(getattr(app, "state", None), "webui_config_dir", None)
    return bool(config_dir) and db_step.is_federated(config_dir)


def _node_step_answered(draft) -> bool:
    """Whether the database step collected a node database at all.

    An empty draft block means it collected NONE: on a federated install the pre-fill leaves the block
    empty rather than offering ``main.yaml``'s cluster database, and a block nobody filled in stays
    empty. A non-empty block is an answer — the node's own connection fields to write.
    """
    return bool(getattr(draft, "database", None))


def _require_join_node_database(draft) -> None:
    """Raise :class:`JoinNodeDatabaseError` when a FEDERATED node has no database of its own.

    Called only when the install is federated (:func:`_federated`); a non-federated node inherits
    ``main.yaml``'s shared database by the CLI's own rule and is never refused.
    """
    if _node_step_answered(draft):
        return
    raise JoinNodeDatabaseError(
        "this installation is federated and the database step (step 3) collected no database for this "
        "node. A node can never inherit the federation's database in main.yaml, so the commit is "
        "refused — enter this node's own database on step 3 and write again.")


def join_database_refusal(draft, *, app=None) -> str | None:
    """The classified reason a joining commit is refused, or ``None`` when it may proceed.

    The non-raising form of :func:`_require_join_node_database`, for a caller that must render the
    refusal rather than build the plan (the Review step, whose plan would otherwise raise). The message
    names the missing piece; nothing has been written when it is returned.
    """
    try:
        if _federated(app):
            _require_join_node_database(draft)
    except JoinNodeDatabaseError as ex:
        return str(ex)
    return None


def _cluster_owns_database(app=None) -> bool:
    """Whether this install's ``main.yaml`` names a database block.

    Read from the application's recorded config directory; an application that names none, or a
    ``main.yaml`` that cannot be read or carries no ``database`` block, answers ``False``.

    A true answer means the node writes no ``database`` block and INHERITS it — but only on a
    non-federated install, where it is the shared database. On a federated install the same block is the
    CLUSTER database and the node must never inherit it (:func:`_join_plan`), so this function is never
    the deciding rule there.
    """
    config_dir = getattr(getattr(app, "state", None), "webui_config_dir", None)
    if not config_dir:
        return False
    try:
        main = configwrite.load_config_file(Path(config_dir) / "main.yaml")
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup join: main.yaml could not be read (%s); writing the node's own database "
                    "URL.", ex)
        return False
    return isinstance(main, dict) and bool(main.get("database"))


def _merge_one(item: PlannedWrite, config_dir) -> WriteOutcome:
    """Merge ONE planned target's top-level keys into the file's own round-trip document.

    :func:`core.utils.configwrite.patch_config_file` loads the file, calls the mutation and writes it
    again under ONE lock, so no other writer can slip between the read and the write. Only the keys
    this commit names are assigned: every other top-level key is a value the file itself supplied and
    is re-emitted unchanged — that is what keeps the other nodes' blocks intact.

    A KEY THE FILE ALREADY HOLDS WITH A DIFFERENT VALUE IS REFUSED, NEVER OVERWRITTEN. These three
    documents are CLUSTER-WIDE — ``nodes.yaml`` is keyed by node name, ``servers.yaml`` by server name
    and ``plugins/scheduler.yaml`` by instance name — and every node reads all three
    (``core/plugin.py``). A server name the operator typed, or an instance name this host happens to
    carry, is enough to collide with another node's row; assigning it would delete that node's
    channels or autostart. The refusal is classified per target (a :class:`JoinCollisionError`) and
    nothing reaches disk — the same protection the node key already has through the boot rule. An
    existing key whose value is EQUAL is allowed: that is an idempotent re-run, a no-op write.
    """
    path = Path(config_dir) / item.target.relpath

    def mutate(document) -> None:
        incoming = dict(item.document or {})
        _refuse_collisions(item.target, incoming, document, path)
        for key, value in incoming.items():
            document[key] = value

    try:
        result = configwrite.patch_config_file(path, mutate, schemas=_schema_files(item.target))
        return WriteOutcome(target=item.target, ok=True, changed=bool(result.changed))
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup join: %s was not merged (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False, error=str(ex))
    except OSError as ex:
        log.warning("Setup join: %s could not be written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False,
                            error=f"Configuration file could not be written ({ex}): {path}")
    except Exception as ex:  # noqa: BLE001 - a validator-side error is ONE classified outcome
        log.warning("Setup join: %s could not be merged (%s).", path, ex, exc_info=True)
        return WriteOutcome(target=item.target, ok=False,
                            error=f"Configuration file could not be written: {path}")


def _refuse_collisions(target: Target, incoming: dict, document, path) -> None:
    """Raise :class:`JoinCollisionError` for the first ``incoming`` key *document* holds DIFFERENTLY.

    Under the file's own write lock (called from the mutation), so no writer can slip between this
    read and the refusal. An empty-existing file (``document`` with nothing) never collides.
    """
    for key, value in incoming.items():
        if key in document and document[key] != value:
            raise JoinCollisionError(
                f"{target.relpath}: '{key}' already exists with different values — refusing to "
                f"overwrite another node's entry.", path=path, key=key)


def _write_one(item: PlannedWrite, config_dir, *, mode: str = INSTALL) -> WriteOutcome:
    """Write ONE planned target, classifying any refusal into the outcome's ``error``.

    JOINING IS REFUSED AT THIS FUNNEL, and that is the point of putting the check here rather than in
    :func:`_join_plan`: every planned target becomes a disk write through this one function, so an
    install-wide file (or a whole-document write of a cluster-keyed one) cannot be reintroduced by a
    later edit of the plan, and a hand-built plan can be proven to write nothing.
    """
    if mode == JOIN:
        if item.target in JOIN_FORBIDDEN_TARGETS:
            log.error("Setup join: refusing to write %s — this installation is configured and that "
                      "file is not this node's to change.", item.target.relpath)
            return WriteOutcome(target=item.target, ok=False,
                                error=f"{item.target.relpath} is not written while joining an "
                                      f"existing installation")
        if item.target in JOIN_MERGE_TARGETS and not item.merge:
            log.error("Setup join: refusing a whole-document write of %s — it is shared by every "
                      "node, so it is only ever merged.", item.target.relpath)
            return WriteOutcome(target=item.target, ok=False,
                                error=f"{item.target.relpath} is merged while joining an existing "
                                      f"installation, never rewritten")
    if item.merge:
        return _merge_one(item, config_dir)
    path = Path(config_dir) / item.target.relpath
    try:
        if item.secret is not None:
            key, value = item.secret
            utils.create_secret_dir(str(config_dir))
            utils.set_password(key, value, str(config_dir))
            return WriteOutcome(target=item.target, ok=True, changed=True)
        os.makedirs(str(path.parent), exist_ok=True)
        result = configwrite.save_config_file(path, item.document,
                                             schemas=_schema_files(item.target))
        return WriteOutcome(target=item.target, ok=True, changed=bool(result.changed))
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: %s was not written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False, error=str(ex))
    except OSError as ex:
        log.warning("Setup tour: %s could not be written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False,
                            error=f"Configuration file could not be written ({ex}): {path}")
    except Exception as ex:  # noqa: BLE001 - a validator-side error is ONE classified outcome
        # Anything a validator raises during the write — an AttributeError on a process global, a
        # future schema change — is classified here per target. It must not escape as a bare 500:
        # the reason stays in the log and the page names the target that was not written.
        log.warning("Setup tour: %s could not be written (%s).", path, ex, exc_info=True)
        return WriteOutcome(target=item.target, ok=False,
                            error=f"Configuration file could not be written: {path}")


def write(plan_items: list[PlannedWrite], config_dir, *, mode: str = INSTALL,
          closing: Target = MAIN) -> WriteReport:
    """Write every planned target through the ONE primitive, and report per target.

    *closing* is the target that makes this commit finished — ``main.yaml`` on a first run, the node's
    ``nodes.yaml`` merge while joining. It is written ONLY when every target before it reached the
    disk: a refusal anywhere else must leave the mode's window open, so the operator can fix the
    reason and write again. A skipped closing target is reported as such — never as a success.
    """
    report = WriteReport(closing=closing)
    blocked = False
    for item in plan_items:
        if item.target is closing and blocked:
            report.outcomes.append(WriteOutcome(target=item.target, ok=False, skipped=True))
            continue
        outcome = _write_one(item, config_dir, mode=mode)
        report.outcomes.append(outcome)
        if not outcome.ok:
            blocked = True
    return report
