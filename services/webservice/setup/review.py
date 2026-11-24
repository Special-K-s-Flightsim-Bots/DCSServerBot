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

WHAT STEP 9 ADDS — THREE WRITES, ALL BEFORE THE CLOSING FILE. The ticked plugin templates become
``main.yaml``'s ``opt_plugins`` (the OPTIONAL names only, unioned with what that key already lists: a
default plugin is never added there); every plugin a ticked template enables whose shipped
``samples/plugins/<plugin>.yaml`` exists is seeded into ``config/plugins/<plugin>.yaml``, and a file
that is already on disk is never overwritten — it is reported as skipped; and the extension answers
travel in ONE NESTED merge into ``nodes.yaml``: the SRS / Tacview / LotAtC installation paths on the
node, and each instance's own SRS port on its instance row. The nested merge fills in only the keys
the document does not carry yet, so every extension setting the file already holds survives.

A plugin's own schema cannot be checked here. ``plugins/<name>/schemas/*_schema.yaml`` validate
through ``func: is_node`` / ``is_element``, which read the install's ``nodes.yaml`` and
``servers.yaml`` — files a first run has not written yet — so a seeded sample is written without a
schema check, exactly as ``plugins/scheduler.yaml`` is (see the note above ``SCHEDULER``).

THE SENTENCES A WRITE REPORTS ARE TRANSLATED WHERE THEY ARE BUILT. ``WriteOutcome.note`` and
``WriteOutcome.error`` are what the Review screen prints beside a target, so each one is a msgid the
four ``webui`` catalogs carry and :func:`_sentence` fills in AFTER translating it. A commit reads the
console language of its own request, so a German console reads German notes; log lines stay English.

AN INSTANCE ROW THIS COMMIT CREATES IS ALSO SEEDED, AND ONLY SUCH A ROW. ``nodes.yaml`` alone leaves a
hand-added instance inert: the bot reads its server NAME and its DCS port out of the instance's own
``Config/serverSettings.lua``. So a managed instance whose ``Saved Games`` folder does not exist when
the commit reaches it gets the folder, the three ``Config`` files (copied from the config directory
when it carries them, written minimal otherwise) — ``NodeImpl.add_instance``'s own shape — and its
three port keys in ``nodes.yaml`` (:class:`InstanceSeed`, :func:`_seed_instance`). A folder that
already EXISTS is left byte-untouched whatever it holds: ``serverSettings.lua``'s absence there is the
deliberate "this server is unlinked" state (``InstanceImpl.set_server(None)`` deletes it), so re-seeding
it would re-bind a server the operator unbound.

WHAT IS NOT DONE HERE. No download, no install and no ``MissionScripting.lua`` edit. Nothing is
written outside the config directory EXCEPT the per-instance ``Saved Games`` tree above — the
command line installer's SRS ``Config/SRS.cfg`` copy is still deliberately not reproduced, so an
instance's SRS configuration stays a console-page edit. The SRS installation path and each instance's
derived port are written to ``nodes.yaml``.
"""

from __future__ import annotations

import logging
import luadata
import os
import platform
import shutil

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import utils
from core.utils import configwrite, db_setup, setup_config

from .. import configgen, i18n, plugin_registry
from . import apps as apps_step
from . import community as community_step
from . import console as console_step
from . import database as db_step
from . import discord as discord_step
from .discord import CLIENT_SECRET_KEY
from . import join as join_step
from . import node as node_step
from . import plugins as plugins_step
from . import servers as servers_step
from .boot import INSTALL, JOIN
from .state import SetupDraft

__all__ = [
    "REPO_ROOT",
    "CONFIG_PREFIX",
    "DEFAULT_CONSOLE_PORT",
    "SRS_PORT_BASE",
    "SRS_PORT_STEP",
    "PLUGIN_SAMPLES",
    "WEBGUI_PORT_BASE",
    "DCS_PORT_BASE",
    "WEBGUI_PORT_STEP",
    "DCS_PORT_STEP",
    "INSTANCE_CONFIG_FILES",
    "Target",
    "MAIN",
    "BOT",
    "NODES",
    "EXTENSIONS",
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
    "plugin_target",
    "enabled_plugins",
    "optional_plugins",
    "InstancePorts",
    "InstanceSeed",
    "SeededInstance",
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

#: The operator-facing sentences a write report carries — ``WriteOutcome.note`` and
#: ``WriteOutcome.error``, as the Review screen renders them. Marked for extraction with ``i18n._``
#: and translated where the sentence is BUILT, because each carries a value (an instance's name, a
#: path, an exception's own text) that is interpolated AFTER translation. Log lines stay English.
T_SRS_NOT_FOUND = i18n._("{instance}: SRS was not found — no port allocated")
T_WRITE_FAILED = i18n._("Configuration file could not be written ({ex}): {path}")
T_ALREADY_EXISTS = i18n._("{path} already exists — left untouched")
T_REFUSED_EARLIER = i18n._("{path} was refused earlier in this commit — not written")
T_JOIN_NOT_WRITTEN = i18n._("{path} is not written while joining an existing installation")
T_JOIN_MERGED_ONLY = i18n._("{path} is merged while joining an existing installation, never rewritten")
T_JOIN_COLLISION = i18n._("{path}: '{key}' already exists with different values — refusing to "
                          "overwrite another node's entry.")
T_INSTANCES_CREATED = i18n._("Saved Games instances created: {created}")
T_INSTANCES_NONE = i18n._("Saved Games instances created: none")
T_INSTANCE_NOT_CREATED = i18n._("{instance}: the instance folder could not be created ({ex})")


def _sentence(language: str | None, msgid: str, **values) -> str:
    """One operator-facing sentence in *language*: translated first, its values filled in after.

    ``None``, or a language that ships no catalog, answers the msgid itself — English is the source
    language and every English string IS its msgid. A catalog line whose placeholders do not match
    falls back to the English sentence rather than raising into the commit.
    """
    text = i18n.translations_for(language or i18n.BASE_LANGUAGE).gettext(msgid)
    if not values:
        return text
    for template in (text, msgid):
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            continue
    return msgid


#: The repository root, four directories up from this module — where the shipped schemas live.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The prefix the Review screen shows in front of a target's relative path.
CONFIG_PREFIX = "config"

#: The console's port when the application did not record the one it served on — the stable port the
#: admin console uses, so the URL the operator already has keeps working.
DEFAULT_CONSOLE_PORT = 9876

#: The SRS radio port this commit starts from when the ``nodes.yaml`` on disk names none, and the step
#: between two instances — the installer's own numbering (``install.py``: 5002, then 5004, 5006, …).
#: The voice-chat choice consumes no port, so it does not advance the count.
SRS_PORT_BASE = 5002
SRS_PORT_STEP = 2

#: The shipped plugin samples and the config directory they are seeded into.
PLUGIN_SAMPLES = REPO_ROOT / "samples" / "plugins"
PLUGIN_CONFIG_DIR = "plugins"


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
DISCORD_CLIENT_SECRET = Target(key=CLIENT_SECRET_KEY,
                               relpath=f".secret/{CLIENT_SECRET_KEY}.pkl", schema=None)

#: The configuration files, in the order they are written. ``main.yaml`` is NOT here on purpose:
#: it is written last, after every file below it succeeded — see :func:`plan`.
#:
#: TWO TARGETS CARRY NO SCHEMA, AND THAT IS DELIBERATE — the bot cannot validate either file before
#: a first-time setup has written it:
#:
#: * ``nodes.yaml`` (``schemas/nodes_schema.yaml``): all three per-instance port keys ARE written
#:   (:func:`instance_ports`), but the schema's ``func: dir_exists`` on an instance's ``home`` would
#:   refuse the file at the moment it is planned — the folder is created right after the write
#:   (:func:`_seed_instance`), because it has to carry the ports this same commit mints.
#: * ``plugins/scheduler.yaml`` (``plugins/scheduler/schemas/scheduler_schema.yaml``): its
#:   ``check_main_structure`` reads the install's EXISTING ``nodes.yaml`` and ``servers.yaml`` through
#:   ``core/utils/validators.py``'s ``NodeData``, which on a first-time setup do not exist yet.
BOT = Target(key="bot", relpath="services/bot.yaml",
             schema="services/bot/schemas/bot_schema.yaml")
NODES = Target(key="nodes", relpath="nodes.yaml", schema=None)
#: ``nodes.yaml``'s ``extensions`` block and each instance's SRS port — merged into the node's own
#: block AFTER :data:`NODES` is written, and only over the keys the document does not carry yet.
EXTENSIONS = Target(key="extensions", relpath="nodes.yaml", schema=None)
SERVERS = Target(key="servers", relpath="servers.yaml", schema="schemas/servers_schema.yaml")
SCHEDULER = Target(key="scheduler", relpath="plugins/scheduler.yaml", schema=None)
CLOUD = Target(key="cloud", relpath="plugins/cloud.yaml",
               schema="plugins/cloud/schemas/cloud_schema.yaml")
WEBSERVICE = Target(key="webservice", relpath="services/webservice.yaml",
                    schema="services/webservice/schemas/webservice_schema.yaml")

#: The one file that closes the setup window. Always the last write of a commit.
MAIN = Target(key="main", relpath="main.yaml", schema="schemas/main_schema.yaml")

#: The optional configuration files, in write order (an absent one is simply not written).
FILE_TARGETS = (BOT, NODES, EXTENSIONS, SERVERS, SCHEDULER, CLOUD, WEBSERVICE)

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
    ``nested`` merges the document's mapping keys one level at a time and fills in ONLY the keys the
    file does not carry yet (implies ``merge``); it is how the step 9 extension answers reach the
    node's block without disturbing what the file already holds.

    ``create_only`` writes the file only when it is ABSENT — an existing file is left untouched and
    reported as skipped. It is the seeding rule for ``config/plugins/<plugin>.yaml``.

    ``notes`` are operator-facing sentences a SUCCESSFUL write reports about what it deliberately left
    out (an SRS instance whose installation is unknown gets no port); they land on the outcome's
    ``note`` and never turn the write into a failure.

    ``unported`` is the machine-readable half of those notes: the instances they name, so a screen can
    act on the fact (the Done screen names them) without parsing a translated sentence.

    ``instance_seeds`` is what the write does BESIDES landing the file, and only the ``nodes.yaml``
    write carries it: once that file is on disk, every instance row it named whose ``Saved Games``
    folder does not exist is created and seeded (:func:`_seed_instance`).
    """

    target: Target
    document: Any = None
    #: ``(key, value)`` for a secret write, ``None`` for a file write
    secret: tuple[str, str] | None = None
    #: whether the write merges into the existing document instead of replacing it
    merge: bool = False
    #: whether the merge fills in only the keys the document does not carry yet (implies ``merge``)
    nested: bool = False
    #: whether an existing target file is left untouched instead of replaced
    create_only: bool = False
    #: what the write left out, reported on the outcome's ``note`` when it succeeds
    notes: tuple[str, ...] = ()
    #: the instances those notes are about (an SRS instance that got no port)
    unported: tuple[str, ...] = ()
    #: the instance rows whose folder this write creates and seeds once the file has landed
    instance_seeds: tuple[InstanceSeed, ...] = ()


@dataclass
class WriteOutcome:
    """What happened to one target.

    ``ok`` is whether it reached the disk, ``changed`` whether the write actually altered anything
    (the primitive's no-op rule answers ``False`` for a file already in the shape we would write),
    ``skipped`` whether it was deliberately not attempted (an earlier file was refused, or the file
    was already there), ``error`` the operator-facing reason a FAILED write names, and ``note`` the
    operator-facing reason a SKIPPED one names — or, on a successful one, what it deliberately left
    out (:attr:`PlannedWrite.notes`). ``error`` and ``note`` never appear on the same outcome.

    ``unported`` carries the instances a successful write's note is about, so a caller can name them
    without reading the sentence (:attr:`PlannedWrite.unported`).

    ``created`` and ``instance_errors`` are the same kind of fact for the instance folders a
    ``nodes.yaml`` write creates (:func:`_seed_instances`): the instances this commit created with the
    files it seeded into each, and — one operator sentence each — the instances whose folder could not
    be created. Neither is a refusal: the file itself landed, so the commit is finished.
    """

    target: Target
    ok: bool = False
    changed: bool = False
    skipped: bool = False
    error: str = ""
    #: why a write was skipped — the reason, not a failure
    note: str = ""
    #: the instances that write gave no SRS port
    unported: tuple[str, ...] = ()
    #: the instances whose Saved Games folder this write created, with the files it seeded
    created: tuple[SeededInstance, ...] = ()
    #: the instances whose folder could not be created — one sentence each, named
    instance_errors: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """One of ``"written"``, ``"skipped"``, ``"failed"`` — the three outcomes, never two at once."""
        if self.skipped:
            return "skipped"
        return "written" if self.ok else "failed"


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
    def skipped(self) -> list[WriteOutcome]:
        """The targets that were deliberately not written (each carries its own ``note``)."""
        return [outcome for outcome in self.outcomes if outcome.skipped]

    @property
    def failed(self) -> list[WriteOutcome]:
        """Every target that did not reach the disk — a skipped one included (it has a ``note``)."""
        return [outcome for outcome in self.outcomes if not outcome.ok]


def node_name(app=None) -> str:
    """The node name ``nodes.yaml`` is keyed on.

    The launcher's own name, recorded on the application at start (``webui_setup_node``); the
    hostname is the fallback, which is what the launcher passes by default anyway.
    """
    recorded = str(getattr(getattr(app, "state", None), "webui_setup_node", "") or "").strip()
    return recorded or platform.node()


# ------------------------------------------------------------------------------- step 9's plugins

def plugin_target(plugin: str) -> Target:
    """The target of one seeded plugin file, ``config/plugins/<plugin>.yaml``.

    No schema: a plugin's own schema validates through ``func: is_node`` / ``is_element`` and reads the
    install's ``nodes.yaml`` and ``servers.yaml``, which a first run has not written yet (see the
    module docstring). The key is namespaced so it can never collide with a static target's.
    """
    return Target(key=f"plugin:{plugin}", relpath=f"{PLUGIN_CONFIG_DIR}/{plugin}.yaml", schema=None)


def enabled_plugins(answers: plugins_step.PluginAnswers,
                    templates: Sequence[plugins_step.Template] | None = None) -> list[str]:
    """Every plugin the ticked templates switch on, in template-tile order, without duplicates.

    A ticked id that names no shipped template contributes nothing (the tile list decides what
    exists), and so does a template that shares a plugin with one earlier on the rail.
    """
    catalogue = plugins_step.load_templates() if templates is None else templates
    chosen = {str(item) for item in answers.templates}
    plugins: list[str] = []
    for template in catalogue:
        if template.id not in chosen:
            continue
        for plugin in template.plugins:
            if plugin not in plugins:
                plugins.append(plugin)
    return plugins


def _listed_opt_plugins(app=None) -> list[str]:
    """``opt_plugins`` as ``main.yaml`` on disk carries it; ``[]`` when the file or the key is absent."""
    config_dir = _config_dir_of(app)
    if not config_dir:
        return []
    try:
        main = configwrite.load_config_file(Path(config_dir) / MAIN.relpath)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: main.yaml could not be read (%s); reading no opt_plugins.", ex)
        return []
    listed = main.get("opt_plugins") if isinstance(main, Mapping) else None
    if not isinstance(listed, (list, tuple)):
        return []
    return [str(name) for name in listed]


def optional_plugins(draft: SetupDraft, *, app=None) -> list[str]:
    """``main.yaml``'s ``opt_plugins``: what that key already lists, plus the templates' OPTIONAL ones.

    The union, in that order: an entry the file already carries is never dropped or re-ordered, and a
    name is never listed twice. A DEFAULT plugin (:func:`services.webservice.plugin_registry.
    default_plugins` — the bot's own ``DEFAULT_PLUGINS``) is never added: ``main.yaml``'s absent
    ``plugins`` key already loads every default. An unreadable default set leaves the list as the
    file has it rather than guessing which names are default.
    """
    result = _listed_opt_plugins(app)
    seen = {name.lower() for name in result}
    defaults = {str(name).lower() for name in plugin_registry.default_plugins()}
    if not defaults:
        log.warning("Setup tour: the bot's default plugin set could not be read; listing only the "
                    "optional names read back from main.yaml.")
        return result
    for plugin in enabled_plugins(plugins_step.answers_from_mapping(draft.plugins)):
        key = plugin.lower()
        if key in defaults or key in seen:
            continue
        result.append(plugin)
        seen.add(key)
    return result


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


def _node_document(draft: SetupDraft, app=None, *, include_database: bool = True,
                   nodes: Mapping | None = None) -> dict:
    """The whole ``nodes.yaml`` document: this node, its database URL, DCS and its instances.

    NO ``extensions`` block and NO per-instance SRS keys: step 9's extension answers arrive in the
    NESTED merge that follows this write (:func:`_extensions_document`), so this document carries only
    what brings the servers up — the node, and one ``instances`` entry per managed instance with its
    ``home`` and its three port keys (``bot_port``, ``webgui_port``, ``dcs_port``), the three the
    instance schema requires.

    *nodes* is the pre-run ``nodes.yaml`` document the plan already read (:func:`_nodes_on_disk`), so
    one commit derives its bot ports and its SRS ports from ONE read; omitted, it is read here.

    THIS IS A WHOLE DOCUMENT, AND THAT IS ONLY EVER CORRECT ON A FIRST RUN. On a joining install it
    would drop every other node's block; the joining plan merges the same key instead
    (:func:`_join_plan`).
    """
    return {node_name(app): _node_block(draft, app, include_database=include_database, nodes=nodes)}


def _node_block(draft: SetupDraft, app=None, *, include_database: bool = True,
                nodes: Mapping | None = None) -> dict:
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
    instances = servers_step.instance_blocks(servers_answers, base=_bot_port_base(app, nodes=nodes))
    if instances:
        # the three port keys the schema requires, from ONE calculation (:func:`instance_ports`) — the
        # bot port it yields is the same number the builder above just wrote
        ports = instance_ports(servers_answers, app=app, nodes=nodes)
        for name, block in instances.items():
            block["bot_port"] = ports[name].bot
            block["webgui_port"] = ports[name].webgui
            block["dcs_port"] = ports[name].dcs
        node["instances"] = instances
    return node


def _bot_port_base(app=None, *, nodes: Mapping | None = None) -> int:
    """The first bot port this commit may assign, given the ``nodes.yaml`` already on disk.

    Read from the installation's own config directory, because the port the commit writes must be the
    port the step RENDERED (``setup.tour._bot_port_base`` reads the same file the same way). *nodes* is
    the document a plan build already read (:func:`_nodes_on_disk`); omitted, it is read here. An
    application that records no directory, or a file that is absent or unreadable, answers the
    installer's own first port — which is what a first-time setup gets.
    """
    document = _nodes_on_disk(app) if nodes is None else nodes
    return servers_step.bot_port_base(document)


# ------------------------------------------------------------------- the instances a commit creates

#: The first console and DCS port, and the step between two instances — ``NodeImpl.add_instance``'s own
#: rule: ``webgui_port`` from 8088 +2, ``dcs_port`` from 10308 +10. The bot port follows the step's own
#: base (``servers.DEFAULT_BOT_PORT``, +1 per instance), so it is not a second constant here.
WEBGUI_PORT_BASE = 8088
DCS_PORT_BASE = 10308
WEBGUI_PORT_STEP = 2
DCS_PORT_STEP = 10

#: The files an instance's ``Config`` directory carries, in ``add_instance``'s own order.
INSTANCE_CONFIG_FILES = ("autoexec.cfg", "serverSettings.lua", "options.lua")

#: The crash report mode ``add_instance`` writes into every fresh ``autoexec.cfg``.
AUTOEXEC_CRASH_REPORT_MODE = "silent"


@dataclass(frozen=True)
class InstancePorts:
    """The three ports one ``nodes.yaml`` instance row carries: bot, console and DCS listener."""

    bot: int
    webgui: int
    dcs: int


@dataclass(frozen=True)
class InstanceSeed:
    """One managed instance row a commit may create: server name, folder, DCS port, UPnP answer.

    ``name`` is the server NAME (the row's name, which is also its ``servers.yaml`` key) — the value
    the seeded ``serverSettings.lua`` carries; ``home`` is the ``Saved Games`` folder the row names.
    """

    name: str
    home: str
    port: int
    use_upnp: bool


@dataclass(frozen=True)
class SeededInstance:
    """One instance whose folder THIS commit created, with the files it seeded into ``Config``."""

    name: str
    files: tuple[str, ...]


def _declared_ports(nodes: Mapping | None, key: str) -> list[int]:
    """Every *key* port a ``nodes.yaml`` document already assigns, over ALL of its nodes.

    An instance whose key is missing or not a number contributes nothing.
    """
    ports: list[int] = []
    for block in (nodes or {}).values():
        instances = block.get("instances") if isinstance(block, Mapping) else None
        for instance in (instances or {}).values() if isinstance(instances, Mapping) else ():
            value = instance.get(key) if isinstance(instance, Mapping) else None
            try:
                ports.append(int(str(value)))
            except (TypeError, ValueError):
                continue
    return ports


def _next_port(ports: list[int], base: int, step: int) -> int:
    """The first port of one kind: one STEP above the highest the document names, else *base*."""
    return max(ports) + step if ports else base


def instance_ports(answers: servers_step.ServerAnswers, *, app=None,
                   nodes: Mapping | None = None) -> dict[str, InstancePorts]:
    """The three ports of every MANAGED instance row, from what ``nodes.yaml`` already declares.

    ONE calculation for the three keys. The bot port starts at :func:`_bot_port_base` — the base the
    step rendered its own bot ports from — and the console and DCS ports at ``add_instance``'s own
    numbers: two above the highest declared ``webgui_port`` and ten above the highest declared
    ``dcs_port``, or 8088 / 10308 when the document names none. The bot port advances one per row,
    the console port two and the DCS port ten, so two new instances of an empty install get
    6666/6667, 8088/8090 and 10308/10318, and no port repeats.

    The DCS port returned here is the one BOTH the ``nodes.yaml`` row and the seeded
    ``serverSettings.lua`` carry: a second derivation is never written anywhere.
    """
    document = _nodes_on_disk(app) if nodes is None else nodes
    bot_base = _bot_port_base(app, nodes=document)
    webgui_base = _next_port(_declared_ports(document, "webgui_port"), WEBGUI_PORT_BASE,
                             WEBGUI_PORT_STEP)
    dcs_base = _next_port(_declared_ports(document, "dcs_port"), DCS_PORT_BASE, DCS_PORT_STEP)
    return {row.instance: InstancePorts(bot=bot_base + index,
                                        webgui=webgui_base + WEBGUI_PORT_STEP * index,
                                        dcs=dcs_base + DCS_PORT_STEP * index)
            for index, row in enumerate(servers_step.managed_rows(answers))}


def instance_seeds(draft: SetupDraft, *, app=None,
                   nodes: Mapping | None = None) -> tuple[InstanceSeed, ...]:
    """One seed per MANAGED instance row: its server name, folder, DCS port and the UPnP answer.

    Every row is listed. Whether its folder is CREATED is decided at the write
    (:func:`_seed_instance`), because a folder that exists by then is an instance this commit must not
    touch. *nodes* is the pre-run document the plan already read; omitted, it is read here.
    """
    servers_answers = servers_step.answers_from_mapping(draft.servers)
    ports = instance_ports(servers_answers, app=app, nodes=nodes)
    use_upnp = bool(node_step.answers_from_mapping(draft.node).use_upnp)
    return tuple(InstanceSeed(name=row.name,
                              home=os.path.join(servers_step.SAVED_GAMES, row.instance),
                              port=ports[row.instance].dcs,
                              use_upnp=use_upnp)
                 for row in servers_step.managed_rows(servers_answers))


def _lua_table(document: Mapping, root: str) -> str:
    """One ``<root> = {…}`` document, through the serializer the bot writes its settings with."""
    return f"{root} = " + luadata.serialize(document, indent="\t", indent_level=0) + "\n"


def _server_settings_text(name: str, port: int) -> str:
    """A minimal ``serverSettings.lua``: the server NAME and its DCS ``port``, nothing else."""
    return _lua_table({"name": name, "port": int(port)}, "cfg")


def _options_text() -> str:
    """A minimal ``options.lua``: the four keys ``ServerImpl.options`` setdefaults."""
    return _lua_table({"graphics": {"visibRange": "High"},
                       "plugins": {},
                       "difficulty": {},
                       "miscellaneous": {"autologin": True}}, "options")


def _autoexec_text(use_upnp: bool) -> str:
    """A minimal ``autoexec.cfg``: the crash report mode, and the UPnP switch when it is off."""
    lines = [f'crash_report_mode = "{AUTOEXEC_CRASH_REPORT_MODE}"']
    if not use_upnp:
        lines += ["if not net then net = {} end", "net.use_upnp = false"]
    return "\n".join(lines) + "\n"


def _ensure_identity(path: str, seed: InstanceSeed) -> None:
    """Add the NAME or the DCS port to a COPIED ``serverSettings.lua`` that carries neither.

    A copied file keeps every value it holds: only a key it does not carry is written, so an
    instance always boots with a name and a port while the operator's own template stays as it is.
    """
    try:
        document = luadata.read(path, encoding="utf-8")
    except Exception as ex:  # noqa: BLE001 - an unparsable copy is left exactly as it was copied
        log.warning("Setup tour: '%s' could not be read back (%s); leaving it as copied.", path, ex)
        return
    if not isinstance(document, dict):
        return
    missing = False
    if not document.get("name"):
        document["name"] = seed.name
        missing = True
    if not document.get("port"):
        document["port"] = int(seed.port)
        missing = True
    if not missing:
        return
    with open(path, mode="w", encoding="utf-8") as outfile:
        outfile.write(_lua_table(document, "cfg"))


def _seed_instance(seed: InstanceSeed, config_dir) -> tuple[str, ...]:
    """Create ONE instance's folder and seed the files a fresh instance needs.

    A folder that already EXISTS is left byte-untouched — ``Config/serverSettings.lua`` included: its
    absence there is the deliberate "this server is unlinked" state, so only a folder this call
    creates is ever written to.

    The three files are copied from *config_dir* when it carries them (``add_instance``'s own copy
    when no template instance is named), and a copied ``serverSettings.lua`` only gains the name or
    the port it lacks (:func:`_ensure_identity`). Each file still absent afterwards is written
    minimal — the ``serverSettings.lua`` carrying the row's NAME and port, the ``options.lua`` with
    the keys the bot setdefaults, the ``autoexec.cfg`` with the crash report mode and the UPnP answer.

    Returns the names of the files seeded, in the order they were written.
    """
    if os.path.isdir(seed.home):
        return ()
    target_dir = os.path.join(seed.home, "Config")
    os.makedirs(target_dir, exist_ok=True)
    seeded: list[str] = []
    for filename in INSTANCE_CONFIG_FILES:
        source = os.path.join(config_dir, filename)
        if not os.path.isfile(source):
            continue
        destination = os.path.join(target_dir, filename)
        shutil.copy2(source, destination)
        if filename == "serverSettings.lua":
            _ensure_identity(destination, seed)
        seeded.append(filename)
    minimal = {
        "serverSettings.lua": lambda: _server_settings_text(seed.name, seed.port),
        "options.lua": _options_text,
        "autoexec.cfg": lambda: _autoexec_text(seed.use_upnp),
    }
    for filename in INSTANCE_CONFIG_FILES:
        path = os.path.join(target_dir, filename)
        if os.path.exists(path):
            continue
        with open(path, mode="w", encoding="utf-8") as outfile:
            outfile.write(minimal[filename]())
        seeded.append(filename)
    return tuple(seeded)


def _seed_instances(item: PlannedWrite, outcome: WriteOutcome, config_dir, *,
                    language: str | None = None) -> None:
    """Create and seed every instance row of a write that just landed, reported on that outcome.

    ONE instance failing never aborts the others and never refuses the commit: its sentence lands on
    the outcome's ``instance_errors`` and in its note, and the closing file is still written. The
    facts a screen needs — which instances were created and what each seed wrote — are the outcome's
    ``created`` tuple, so naming them never means parsing the note.
    """
    created: list[SeededInstance] = []
    errors: list[str] = []
    for seed in item.instance_seeds:
        try:
            files = _seed_instance(seed, config_dir)
        except OSError as ex:
            log.warning("Setup tour: the instance '%s' could not be created in '%s' (%s).",
                        seed.name, seed.home, ex)
            errors.append(_sentence(language, T_INSTANCE_NOT_CREATED, instance=seed.name, ex=ex))
            continue
        if files:
            log.info("Setup tour: created the instance '%s' and seeded %s.", seed.name,
                     ", ".join(files))
            created.append(SeededInstance(name=seed.name, files=files))
    outcome.created = tuple(created)
    outcome.instance_errors = tuple(errors)
    note = (_sentence(language, T_INSTANCES_CREATED,
                      created=", ".join(f"{entry.name} ({', '.join(entry.files)})"
                                        for entry in created))
            if created else _sentence(language, T_INSTANCES_NONE))
    outcome.note = " ".join(part for part in (outcome.note, note, *errors) if part)


def _bot_document(draft: SetupDraft) -> dict:
    """``services/bot.yaml``: the Discord role mapping, or the no-Discord community one."""
    if draft.discord:
        return discord_step.bot_block(discord_step.answers_from_mapping(draft.discord_bot),
                                      discord_step.roles_from_mapping(draft.roles))
    return community_step.bot_block(community_step.answers_from_mapping(draft.community))


def _servers_document(draft: SetupDraft) -> dict:
    """``servers.yaml``: one channels block per managed server (empty on the no-Discord path).

    An instance whose step 9 radio chose the DCS voice chat also carries its switch
    (``serverSettings.advanced.voice_chat_server``); the SRS choice writes none here — that instance's
    port lives on its ``nodes.yaml`` row (:func:`_srs_ports`).
    """
    servers_answers = servers_step.answers_from_mapping(draft.servers)
    central_admin = (discord_step.roles_from_mapping(draft.roles).central_admin
                     if draft.discord else False)
    entries = servers_step.servers_entries(servers_answers, discord=draft.discord,
                                           central_admin=central_admin)
    return _with_voice_chat(entries, servers_answers,
                            plugins_step.answers_from_mapping(draft.plugins))


def _with_voice_chat(entries: dict, servers_answers: servers_step.ServerAnswers,
                     plugin_answers: plugins_step.PluginAnswers) -> dict:
    """Set ``serverSettings.advanced.voice_chat_server`` on every row that chose the DCS voice chat.

    That is the key the VoiceChat extension reads (``extensions/voicechat/extension.py``:
    ``server.settings['advanced']['voice_chat_server']``) and the one ``schemas/servers_schema.yaml``
    validates. Keyed by the server NAME (``servers.yaml``'s key), from the row that carries the
    instance the radio answer is about; every other key of the entry is kept as the builder wrote it.
    """
    document = {name: dict(entry) for name, entry in entries.items()}
    for row in servers_step.managed_rows(servers_answers):
        if plugin_answers.radio.get(row.instance) != plugins_step.RADIO_VOICE_CHAT:
            continue
        entry = document.setdefault(row.name, {})
        settings = entry.get("serverSettings")
        settings = dict(settings) if isinstance(settings, Mapping) else {}
        advanced = settings.get("advanced")
        advanced = dict(advanced) if isinstance(advanced, Mapping) else {}
        advanced["voice_chat_server"] = True
        settings["advanced"] = advanced
        entry["serverSettings"] = settings
    return document


def _apps_answers(plugin_answers: plugins_step.PluginAnswers) -> apps_step.AppAnswers:
    """Step 9's extension answers as the Apps step's row shape, each row carrying its detected path.

    SRS is ON for a rail whose instances did not all choose the DCS voice chat, Tacview and LotAtC
    from their own ticks. A path nothing was detected at stays empty, so
    :func:`services.webservice.setup.apps.extensions_block` writes no block for it — the file then
    says nothing rather than something wrong.
    """
    detected = {spec.key: apps_step.detect_app(spec) for spec in apps_step.APPS}
    choices = plugin_answers.radio.values()
    srs_on = bool(plugin_answers.radio) and any(choice == plugins_step.RADIO_SRS
                                                for choice in choices)
    return apps_step.AppAnswers(rows=[
        apps_step.AppRow(key="SRS", configured=srs_on, installation=detected.get("SRS") or ""),
        apps_step.AppRow(key="Tacview", configured=plugin_answers.tacview,
                         installation=detected.get("Tacview") or ""),
        apps_step.AppRow(key="LotAtC", configured=plugin_answers.lotatc,
                         installation=detected.get("LotAtC") or ""),
    ])


def _nodes_on_disk(app=None) -> Mapping:
    """The ``nodes.yaml`` this run reads, as a mapping, or ``{}`` when there is none to read.

    Read ONCE per plan build and handed to every reader of it (:func:`_bot_port_base`,
    :func:`_srs_port_base`, :func:`_srs_installed_on_disk`), so the ports and the SRS rules of one
    commit are all derived from one pre-run state. An application that records no config directory, a
    file that is absent, and one that cannot be read all answer ``{}``.
    """
    config_dir = _config_dir_of(app)
    if not config_dir:
        return {}
    path = Path(config_dir) / "nodes.yaml"
    try:
        nodes = configwrite.load_config_file(path)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: '%s' could not be read (%s); deriving the step 9 ports without it.",
                    path, ex)
        return {}
    return nodes if isinstance(nodes, Mapping) else {}


def _existing_srs_ports(nodes: Mapping | None) -> list[int]:
    """Every SRS port the ``nodes`` document's instance rows carry.

    A row whose ``extensions.SRS.port`` is missing or not a number contributes nothing.
    """
    ports: list[int] = []
    for block in (nodes or {}).values():
        instances = block.get("instances") if isinstance(block, Mapping) else None
        for instance in (instances or {}).values() if isinstance(instances, Mapping) else ():
            extensions = instance.get("extensions") if isinstance(instance, Mapping) else None
            srs = extensions.get("SRS") if isinstance(extensions, Mapping) else None
            port = srs.get("port") if isinstance(srs, Mapping) else None
            try:
                ports.append(int(str(port)))
            except (TypeError, ValueError):
                continue
    return ports


def _srs_port_base(app=None, *, nodes: Mapping | None = None) -> int:
    """The first SRS port this commit may assign, given the ``nodes.yaml`` already on disk.

    The ports are allocated two apart, so the base starts one STEP above the highest one the document
    names: an install that already carries 5002 and 5006 hands the next instance 5008, never the 5004
    one of those instances may hold under a different layout. *nodes* is the document a plan build
    already read (:func:`_nodes_on_disk`); omitted, it is read here. A document naming no SRS port — a
    fresh first run in particular — answers the installer's own first port, :data:`SRS_PORT_BASE`.
    """
    document = _nodes_on_disk(app) if nodes is None else nodes
    ports = _existing_srs_ports(document)
    return max(ports) + SRS_PORT_STEP if ports else SRS_PORT_BASE


def _srs_installed_on_disk(app=None, *, nodes: Mapping | None = None) -> bool:
    """Whether this node's ``nodes.yaml`` already names an SRS installation directory.

    *nodes* is the document a plan build already read (:func:`_nodes_on_disk`); omitted, it is read
    here.
    """
    document = _nodes_on_disk(app) if nodes is None else nodes
    node = document.get(node_name(app))
    extensions = node.get("extensions") if isinstance(node, Mapping) else None
    srs = extensions.get("SRS") if isinstance(extensions, Mapping) else None
    installation = srs.get("installation") if isinstance(srs, Mapping) else None
    return bool(str(installation or "").strip())


def _srs_ports(draft: SetupDraft, plugin_answers: plugins_step.PluginAnswers, *,
               installed: bool, app=None, nodes: Mapping | None = None,
               language: str | None = None) -> tuple[dict[str, dict], list[str], list[str]]:
    """Each SRS instance's port, as the ``nodes.yaml`` instance entries' ``extensions`` block.

    *installed* is whether this node's SRS installation is known — the detected path, or one the
    ``nodes.yaml`` on disk already carries. A row whose radio is SRS gets a port ONLY then: without an
    installation ``apps.extensions_block`` writes no block for it, so a port would name a server that
    is never configured. Such a row is reported instead, one note per instance, so the miss is visible.

    Returns the instance entries, one operator sentence per instance that got no port, and those
    instances' names — the machine-readable fact the Done screen names them by.

    Numbered from :func:`_srs_port_base` and two apart per allocated port, as ``install.py`` does. A
    row that chose the DCS voice chat, and a row the rail does not carry, contribute nothing — and the
    voice-chat choice does not advance the count.
    """
    base = _srs_port_base(app, nodes=nodes)
    ports: dict[str, dict] = {}
    notes: list[str] = []
    unported: list[str] = []
    index = 0
    for row in servers_step.managed_rows(servers_step.answers_from_mapping(draft.servers)):
        choice = plugin_answers.radio.get(row.instance)
        if choice != plugins_step.RADIO_SRS:
            continue
        if not installed:
            unported.append(row.instance)
            notes.append(_sentence(language, T_SRS_NOT_FOUND, instance=row.instance))
            continue
        ports[row.instance] = {"extensions": {
            "SRS": {"port": base + SRS_PORT_STEP * index}}}
        index += 1
    return ports, notes, unported


def _extensions_document(draft: SetupDraft, app=None, *, nodes: Mapping | None = None,
                         language: str | None = None
                         ) -> tuple[dict, tuple[str, ...], tuple[str, ...]] | None:
    """The NESTED ``nodes.yaml`` merge of step 9's extension answers, and what it left out.

    Two blocks, both under this node's own key: ``extensions`` — the SRS / Tacview / LotAtC
    installation paths the rows had detected — and each SRS instance's ``extensions.SRS.port``. The
    merge fills in only the keys the file does not carry yet, so an extension setting an existing
    ``nodes.yaml`` already holds (a hand-entered path, a different port) survives this commit.

    The other two halves are one note per SRS row that got no port (:func:`_srs_ports`) and those
    instances' names, carried on the write's own outcome. ``None`` means there is nothing to merge AND
    nothing to report; a document that is empty at the node level is this commit's caller's cue to
    carry the notes on a write that does land (:func:`plan`).
    """
    plugin_answers = plugins_step.answers_from_mapping(draft.plugins)
    block = apps_step.extensions_block(_apps_answers(plugin_answers))
    installed = bool((block.get("SRS") or {}).get("installation")) or _srs_installed_on_disk(
        app, nodes=nodes)
    instances, notes, unported = _srs_ports(draft, plugin_answers, installed=installed, app=app,
                                           nodes=nodes, language=language)
    node: dict[str, Any] = {}
    if block:
        node["extensions"] = block
    if instances:
        node["instances"] = instances
    if not node and not notes:
        return None
    return {node_name(app): node}, tuple(notes), tuple(unported)


def _carries_a_node_block(document: Mapping) -> bool:
    """Whether an extension merge document would actually ADD something to the node's block.

    The document is keyed by node name; a node block with no key at all (``{node: {}}``) is what an
    SRS row with no installation found leaves behind, and merging it is a no-op the plan does not
    need — the notes it carries are reported by the node write instead (see :func:`plan`).
    """
    return any(bool(block) for block in document.values())


def _seed_writes(draft: SetupDraft) -> list[PlannedWrite]:
    """One create-only write per enabled plugin whose shipped sample exists.

    A plugin with no sample writes NO file — the samples are the shipped content, never built here —
    and a plugin whose sample cannot be read is reported and skipped. The write itself refuses an
    existing target, so an operator's own ``config/plugins/<plugin>.yaml`` is never replaced.
    """
    writes: list[PlannedWrite] = []
    for plugin in enabled_plugins(plugins_step.answers_from_mapping(draft.plugins)):
        sample = PLUGIN_SAMPLES / f"{plugin}.yaml"
        if not sample.is_file():
            log.debug("Setup tour: no sample for the %s plugin; writing no plugin file for it.",
                      plugin)
            continue
        try:
            document = configwrite.load_config_file(sample)
        except configwrite.ConfigWriteError as ex:
            log.warning("Setup tour: the sample at '%s' could not be read (%s); not seeding it.",
                        sample, ex)
            continue
        if document is None:
            log.warning("Setup tour: the sample at '%s' is empty; not seeding it.", sample)
            continue
        writes.append(PlannedWrite(target=plugin_target(plugin), document=document,
                                   create_only=True))
    return writes


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

    ``opt_plugins`` is written when step 9 switched at least one OPTIONAL plugin on
    (:func:`optional_plugins`); with nothing optional ticked the key is absent, which is the shape the
    installer writes and what makes the bot load every default.
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
    opt_plugins = optional_plugins(draft, app=app)
    if opt_plugins:
        document["opt_plugins"] = opt_plugins
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


def plan(draft: SetupDraft, *, app=None, mode: str = INSTALL,
         language: str | None = None) -> list[PlannedWrite]:
    """Everything a commit will do, in the order it happens: secrets, files, then the closing file.

    Only targets that are actually wanted appear, so the Review screen's "will be written" list is
    the commit itself rather than a promise. A target that would be empty is left out exactly as the
    installer leaves it out (an empty scheduler mapping, a cloud block nobody opted into, a secret
    that is not held).

    The pre-run ``nodes.yaml`` is read ONCE here and handed to every reader of it, so the bot ports and
    the SRS ports of one build come from one state — the instance rows' three port keys and the seeds
    written once that file lands come from it too (:func:`instance_ports`, :func:`instance_seeds`).

    *language* is the console language the operator-facing sentences of this build are written in;
    ``None`` answers the English source.

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
    # ONE read of the pre-run nodes.yaml, for the bot ports AND step 9's SRS rules
    nodes_on_disk = _nodes_on_disk(app)
    nodes_write = PlannedWrite(target=NODES,
                               document=_node_document(draft, app, nodes=nodes_on_disk),
                               instance_seeds=instance_seeds(draft, app=app, nodes=nodes_on_disk))
    writes.append(nodes_write)
    # step 9's extension answers are merged into the node block this commit just wrote, so they come
    # immediately AFTER it and before anything else — and, like every write here, before main.yaml
    extensions = _extensions_document(draft, app, nodes=nodes_on_disk, language=language)
    if extensions is not None:
        document, notes, unported = extensions
        if _carries_a_node_block(document):
            writes.append(PlannedWrite(target=EXTENSIONS, document=document, merge=True, nested=True,
                                       notes=notes, unported=unported))
        else:
            # nothing to merge at the node level (an SRS row with no installation found, and no other
            # extension ticked): no write is planned, and the notes move onto the node write, which is
            # the one that lands — a note is never dropped for want of a target to carry it
            nodes_write.notes = tuple(nodes_write.notes) + tuple(notes)
            nodes_write.unported = tuple(nodes_write.unported) + tuple(unported)
    writes.append(PlannedWrite(target=SERVERS, document=_servers_document(draft)))
    scheduler = _scheduler_document(draft)
    if scheduler:
        writes.append(PlannedWrite(target=SCHEDULER, document=scheduler))
    cloud = _cloud_document(draft)
    if cloud is not None:
        writes.append(PlannedWrite(target=CLOUD, document=cloud))
    writes.extend(_seed_writes(draft))
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
                               merge=True,
                               instance_seeds=instance_seeds(draft, app=app)))
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


def _merge_one(item: PlannedWrite, config_dir, *, language: str | None = None) -> WriteOutcome:
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

    A NESTED merge (:attr:`PlannedWrite.nested`) never refuses and never overwrites: it fills in only
    the keys the document does not carry yet, recursively, so a value the file already holds — an
    operator's extension path, an instance's port — survives untouched.

    *language* is the console language this outcome's operator sentences are written in.
    """
    path = Path(config_dir) / item.target.relpath

    def mutate(document) -> None:
        incoming = dict(item.document or {})
        if item.nested:
            _fill_missing(document, incoming)
            return
        _refuse_collisions(item.target, incoming, document, path, language=language)
        for key, value in incoming.items():
            document[key] = value

    try:
        result = configwrite.patch_config_file(path, mutate, schemas=_schema_files(item.target))
        return WriteOutcome(target=item.target, ok=True, changed=bool(result.changed),
                            note=" ".join(item.notes), unported=item.unported)
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup join: %s was not merged (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False, error=str(ex))
    except OSError as ex:
        log.warning("Setup join: %s could not be written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False,
                            error=_sentence(language, T_WRITE_FAILED, ex=ex, path=path))
    except Exception as ex:  # noqa: BLE001 - a validator-side error is ONE classified outcome
        log.warning("Setup join: %s could not be merged (%s).", path, ex, exc_info=True)
        return WriteOutcome(target=item.target, ok=False,
                            error=_sentence(language, T_WRITE_FAILED, ex=ex, path=path))


def _fill_missing(document: Any, incoming: Mapping) -> None:
    """Add *incoming*'s keys to *document*, descending where both carry a mapping.

    A key the document already holds keeps its value — at every level — so a nested merge can only
    ever complete a document, never change one.
    """
    for key, value in incoming.items():
        existing = document.get(key) if isinstance(document, Mapping) else None
        if isinstance(value, Mapping) and isinstance(existing, Mapping):
            _fill_missing(existing, value)
        elif key not in document:
            document[key] = value


def _refuse_collisions(target: Target, incoming: dict, document, path,
                       *, language: str | None = None) -> None:
    """Raise :class:`JoinCollisionError` for the first ``incoming`` key *document* holds DIFFERENTLY.

    Under the file's own write lock (called from the mutation), so no writer can slip between this
    read and the refusal. An empty-existing file (``document`` with nothing) never collides.
    """
    for key, value in incoming.items():
        if key in document and document[key] != value:
            raise JoinCollisionError(
                _sentence(language, T_JOIN_COLLISION, path=target.relpath, key=key),
                path=path, key=key)


def _write_one(item: PlannedWrite, config_dir, *, mode: str = INSTALL,
               language: str | None = None) -> WriteOutcome:
    """Write ONE planned target, classifying any refusal into the outcome's ``error``.

    JOINING IS REFUSED AT THIS FUNNEL, and that is the point of putting the check here rather than in
    :func:`_join_plan`: every planned target becomes a disk write through this one function, so an
    install-wide file (or a whole-document write of a cluster-keyed one) cannot be reintroduced by a
    later edit of the plan, and a hand-built plan can be proven to write nothing.

    A ``create_only`` target that already exists is SKIPPED — reported, not written — and the check is
    repeated inside the write (``expect_revision=""``) so a file that appears between the plan and the
    write is a skip too, never a replacement.

    *language* is the console language this outcome's operator sentences are written in.
    """
    if mode == JOIN:
        if item.target in JOIN_FORBIDDEN_TARGETS:
            log.error("Setup join: refusing to write %s — this installation is configured and that "
                      "file is not this node's to change.", item.target.relpath)
            return WriteOutcome(target=item.target, ok=False,
                                error=_sentence(language, T_JOIN_NOT_WRITTEN,
                                                path=item.target.relpath))
        if item.target in JOIN_MERGE_TARGETS and not item.merge:
            log.error("Setup join: refusing a whole-document write of %s — it is shared by every "
                      "node, so it is only ever merged.", item.target.relpath)
            return WriteOutcome(target=item.target, ok=False,
                                error=_sentence(language, T_JOIN_MERGED_ONLY,
                                                path=item.target.relpath))
    if item.merge:
        return _merge_one(item, config_dir, language=language)
    path = Path(config_dir) / item.target.relpath
    if item.create_only and path.exists():
        log.info("Setup tour: %s already exists; leaving it untouched.", path)
        return WriteOutcome(target=item.target, ok=False, skipped=True,
                            note=_sentence(language, T_ALREADY_EXISTS, path=item.target.relpath))
    try:
        if item.secret is not None:
            key, value = item.secret
            utils.create_secret_dir(str(config_dir))
            utils.set_password(key, value, str(config_dir))
            return WriteOutcome(target=item.target, ok=True, changed=True)
        os.makedirs(str(path.parent), exist_ok=True)
        result = configwrite.save_config_file(path, item.document,
                                              schemas=_schema_files(item.target),
                                              expect_revision="" if item.create_only else None)
        return WriteOutcome(target=item.target, ok=True, changed=bool(result.changed),
                            note=" ".join(item.notes), unported=item.unported)
    except configwrite.StaleConfigRevision:
        return WriteOutcome(target=item.target, ok=False, skipped=True,
                            note=_sentence(language, T_ALREADY_EXISTS, path=item.target.relpath))
    except configwrite.ConfigWriteError as ex:
        log.warning("Setup tour: %s was not written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False, error=str(ex))
    except OSError as ex:
        log.warning("Setup tour: %s could not be written (%s).", path, ex)
        return WriteOutcome(target=item.target, ok=False,
                            error=_sentence(language, T_WRITE_FAILED, ex=ex, path=path))
    except Exception as ex:  # noqa: BLE001 - a validator-side error is ONE classified outcome
        # Anything a validator raises during the write — an AttributeError on a process global, a
        # future schema change — is classified here per target. It must not escape as a bare 500:
        # the page names the target that was not written AND the exception's own text, which is the
        # only description of the crash the operator gets.
        log.warning("Setup tour: %s could not be written (%s).", path, ex, exc_info=True)
        return WriteOutcome(target=item.target, ok=False,
                            error=_sentence(language, T_WRITE_FAILED, ex=ex, path=path))


def write(plan_items: list[PlannedWrite], config_dir, *, mode: str = INSTALL,
          closing: Target = MAIN, language: str | None = None) -> WriteReport:
    """Write every planned target through the ONE primitive, and report per target.

    *closing* is the target that makes this commit finished — ``main.yaml`` on a first run, the node's
    ``nodes.yaml`` merge while joining. It is written ONLY when every target before it reached the
    disk: a refusal anywhere else must leave the mode's window open, so the operator can fix the
    reason and write again. A skipped closing target is reported as such — never as a success.

    TWO TARGETS ARE TREATED SPECIALLY, and neither counts as a refusal:

    * a DELIBERATE skip (``PlannedWrite.create_only`` over an existing file, or a target whose file was
      already refused earlier in this commit) is reported with its ``note`` and does not hold the
      closing file back — a plugin file the operator already has is not a reason to leave the
      installation unconfigured;
    * a file whose write was REFUSED earlier in this commit is never written again by a later entry
      (``nodes.yaml`` is planned twice: the node, then its extension block), so a refused document
      cannot come back half-built through the merge that follows it.

    *language* is the console language the operator sentences of the outcomes are written in.
    """
    report = WriteReport(closing=closing)
    blocked = False
    refused_files: set[str] = set()
    for item in plan_items:
        if item.target is closing and blocked:
            report.outcomes.append(WriteOutcome(target=item.target, ok=False, skipped=True))
            continue
        if item.target.relpath in refused_files:
            report.outcomes.append(WriteOutcome(
                target=item.target, ok=False, skipped=True,
                note=_sentence(language, T_REFUSED_EARLIER, path=item.target.relpath)))
            blocked = True
            continue
        outcome = _write_one(item, config_dir, mode=mode, language=language)
        report.outcomes.append(outcome)
        if outcome.ok and item.instance_seeds:
            # the rows are on disk: each one whose folder is still absent is created and seeded here,
            # so a failure names its instance on this outcome without holding the closing file back
            _seed_instances(item, outcome, config_dir, language=language)
        if not outcome.ok and not outcome.skipped:
            blocked = True
            refused_files.add(item.target.relpath)
    return report
