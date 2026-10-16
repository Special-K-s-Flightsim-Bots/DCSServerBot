"""The joining state: ``main.yaml`` exists and THIS node has no block in ``nodes.yaml``.

WHY THIS STATE EXISTS. The bot cannot boot without a ``nodes.yaml`` block for the node it was started
as: ``core/data/impl/nodeimpl.py`` raises ``InstallationException``, and the launcher used to fall
straight into the interactive command line installer. This module is what the setup console offers
instead: the add-node path of ``install.py``, in the browser, writing into the EXISTING
``nodes.yaml``.

WHAT IT NEVER WRITES. This installation is already configured, so the install-wide files are not this
flow's business: ``main.yaml``, ``services/bot.yaml``, ``services/webservice.yaml``,
``plugins/cloud.yaml`` and the install's Discord token are never written by joining (see
:data:`services.webservice.setup.review.JOIN_FORBIDDEN`). Only this node's own block, its servers and
its autostart entries land — and the two cluster-keyed files are MERGED, never replaced.

TWO ROUTES, ASKED FIRST. A NEW node is added. An EXISTING node whose hostname changed is RENAMED
through the installer's own :func:`install.Install.rename_node` — the same call the command line
installer makes, which rewrites the config yamls and migrates the database — never a second
implementation of it.
"""

from __future__ import annotations

import logging
import os

from dataclasses import dataclass
from typing import Any, Mapping

from core import utils
from core.utils import configwrite

from . import database as db_step

__all__ = [
    "MAIN_FILE",
    "NODES_FILE",
    "BOT_FILE",
    "TOKEN_KEY",
    "JoinDiscord",
    "ROUTE_KEY",
    "ROUTE_NEW",
    "ROUTE_RENAME",
    "config_documents",
    "existing_nodes",
    "discord_context",
    "route",
    "set_route",
    "seed_draft",
    "rename_node",
]

log = logging.getLogger(__name__)

#: The two files this mode reads and must not lose: the configured install's own facts.
MAIN_FILE = "main.yaml"
NODES_FILE = "nodes.yaml"

#: The bot's own mapping: its ``no_discord`` flag and its central admin channel both decide what the
#: joining server step asks (see :func:`discord_context`).
BOT_FILE = os.path.join("services", "bot.yaml")

#: The install's bot token in the installer's own secret store (``<config>/.secret/token.pkl``).
TOKEN_KEY = "token"

#: The draft key the add/rename answer is kept under (inside ``SetupDraft.join``).
ROUTE_KEY = "route"

#: Add this node to ``nodes.yaml``.
ROUTE_NEW = "new"

#: Rename an existing node to this node's name.
ROUTE_RENAME = "rename"


def _load(path) -> Mapping:
    """One config file as a parsed mapping; an unreadable or non-mapping document answers ``{}``."""
    try:
        document = configwrite.load_config_file(path)
    except Exception as ex:  # noqa: BLE001 - an unreadable file is "nothing known", never a crash
        log.warning("Setup join: '%s' could not be read (%s).", path, ex)
        return {}
    return document if isinstance(document, Mapping) else {}


def config_documents(config_dir) -> tuple[Mapping, Mapping]:
    """``main.yaml`` and ``nodes.yaml`` as parsed mappings (either is ``{}`` when unreadable)."""
    base = str(config_dir or "config")
    return _load(os.path.join(base, MAIN_FILE)), _load(os.path.join(base, NODES_FILE))


def existing_nodes(config_dir) -> dict[str, Any]:
    """The nodes ``nodes.yaml`` already carries, in file order — the rename question's choices."""
    return dict(config_documents(config_dir)[1])


@dataclass(frozen=True)
class JoinDiscord:
    """The EXISTING installation's Discord facts, read from its shared config.

    The joining flow asks no Discord question (steps 2, 5 and 6 are off its rail), so the channel
    pickers its server step offers are fed from here instead: ``discord`` says whether the
    installation is a Discord one at all, ``token`` is its stored bot token, ``guild_id`` the guild
    ``main.yaml`` names, and ``central_admin`` whether ``bot.yaml`` already carries a central admin
    channel (which is what removes the per-server admin channel from the rows).
    """

    discord: bool = True
    token: str = ""
    guild_id: int | None = None
    central_admin: bool = False


def _read_token(config_dir) -> str:
    """The installation's stored bot token, or ``""`` when no token can be read.

    A missing ``.secret/token.pkl`` is the honest "this install has no token" and raises
    (:func:`core.utils.os.get_password`); an unreadable store answers the same rather than breaking
    a render.
    """
    try:
        return utils.get_password(TOKEN_KEY, str(config_dir))
    except Exception as ex:  # noqa: BLE001 - no readable token is "none", never a render failure
        log.warning("Setup join: the bot token could not be read from '%s' (%s).", config_dir, ex)
        return ""


def _as_id(value) -> int | None:
    """*value* as a Discord id, or ``None`` when it is missing or not a whole number."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def discord_context(config_dir) -> JoinDiscord:
    """Read the installation's Discord context from its SHARED config: ``main.yaml``, ``bot.yaml``
    and the token store.

    A config directory that is not known yields the default context (a Discord install with nothing
    readable), which is exactly the typed-id degrade the step renders without a guild.
    """
    if not config_dir:
        return JoinDiscord()
    main, _ = config_documents(config_dir)
    bot = _load(os.path.join(str(config_dir), BOT_FILE))
    channels = bot.get("channels")
    channels = channels if isinstance(channels, Mapping) else {}
    admin = channels.get("admin")
    return JoinDiscord(
        discord=not bool(bot.get("no_discord")),
        token=_read_token(config_dir),
        guild_id=_as_id(main.get("guild_id")),
        central_admin=admin not in (None, "", -1, "-1"),
    )


def route(draft) -> str | None:
    """The add/rename answer the draft holds, or ``None`` while the question is unanswered."""
    block = getattr(draft, "join", None)
    block = block if isinstance(block, Mapping) else {}
    value = block.get(ROUTE_KEY)
    return str(value) if value in (ROUTE_NEW, ROUTE_RENAME) else None


def set_route(draft, value: str) -> None:
    """Record the add/rename answer (anything else leaves the draft's own mapping alone)."""
    if value not in (ROUTE_NEW, ROUTE_RENAME):
        return
    draft.join = {ROUTE_KEY: value}


def seed_draft(draft, config_dir) -> None:
    """Seed the joining draft ONCE: the database pre-fill, and the step the flow starts at.

    The database step is pre-filled from the URL this install already names
    (:func:`setup.database.cluster_database_url`) — this node's OWN database when ``nodes.yaml``
    carries one, and otherwise ``main.yaml``'s cluster database only when the install is not
    federated — so the step opens with "Use an existing database" pre-selected and every field but
    the password filled in. A draft that already carries a database block is left EXACTLY as it is:
    a re-render, a reload and a restart must never overwrite an answer the operator gave.
    """
    if draft.database:
        return
    main, nodes = config_documents(config_dir)
    url = db_step.cluster_database_url(main, nodes, config_dir)
    draft.database = db_step.prefill_document(url) if url else {}
    draft.step = 0
    draft.save(config_dir)


def rename_node(config_dir, old: str, new: str) -> None:
    """Rename *old* to *new* through the installer's own helper.

    Imported HERE, not at module level: ``install.py`` is the installer's module and pulls the
    command line arguments, rich and the database driver, none of which this console needs before
    this one call.

    The installer's translatable messages resolve through its module-global ``_``, which
    ``Install.install`` sets up — a console call enters past it, so it is initialised here before the
    helper can reach a message.
    """
    import install as install_module

    from core import translations

    if getattr(install_module, "_", None) is None:
        install_module._ = translations.get_translation("install")
    install_module.Install.rename_node(str(config_dir), old, new)
