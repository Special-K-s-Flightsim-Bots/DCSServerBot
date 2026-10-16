"""The setup tour's draft — the answers given so far, kept on disk while the tour is walked.

One file, ``<config_dir>/.setup-draft.yaml``, written through the ONE config-write primitive
(``core.utils.configwrite``) like every other configuration write in the console. It holds the
answers the built steps own — the operator's language, the bot-language decision, auto-update, the
Discord decision, the database block, this node's block, the detected DCS servers with their rows,
the Discord block (guild id, owner, the member-count decision), the role
mapping with its channels, the no-Discord community block, the console account and the Review step's
own per-target write results — plus the furthest COMPLETED step.

SECRETS ARE NOT HERE. A typed password or bot token is a secret the console holds for its process
lifetime only; the draft is plain text in a config directory, so nothing of the sort is written to it
(the console account's PBKDF2 HASH is, because it is what the console itself stores and it is not the
secret).

WHY A FILE, AND NOT THE SESSION. The tour has to survive a browser reload AND a restart of the setup
console. The session cookie is signed with an
ephemeral key unless a secret is configured (``services/webservice/session.py``), so it cannot carry
the draft across a restart. A file can, and it does so WITHOUT touching the boot window: ``main.yaml``
is still the one file that closes setup, and this is not that file.

The draft is deliberately NOT a configuration file the bot reads: nothing in the bot's own loader
opens it (``.setup-draft.yaml`` is a hidden, unlisted name), and the tour's last step — the Review
step, ``setup.review`` — is what turns the answers into the real ``main.yaml`` and the other config
files, and removes the draft once the commit is complete.

FORWARD COMPATIBILITY. The draft is written by several steps built on several cards, so a save here
must never drop a key it does not own: unknown top-level keys and unknown ``main`` sub-keys are
carried through every write. That is what makes "flip Discord off and back on and the answers to the
steps that left the rail are kept" true for keys a later step adds.
"""

from __future__ import annotations

import logging
import os

from pathlib import Path
from typing import Any, Mapping

__all__ = ["DRAFT_FILENAME", "LAST_STEP", "draft_path", "SetupDraft"]

log = logging.getLogger(__name__)

#: The draft's file name, under the config directory. Hidden on purpose: it is tour state, not a
#: configuration file an operator opens.
DRAFT_FILENAME = ".setup-draft.yaml"

#: The last step of the tour (the Review-and-write screen). A draft at this step is a finished tour.
LAST_STEP = 9

#: The top-level keys this module owns; every other key is carried through untouched (see the module
#: docstring).
_OWNED_KEYS = ("step", "discord", "language", "main", "database", "node", "servers",
               "discord_bot", "roles", "community", "console", "join", "review")

#: The ``main`` sub-keys this module owns; the rest of the block is carried through untouched.
_OWNED_MAIN_KEYS = ("language", "autoupdate")


def draft_path(config_dir) -> Path:
    """The draft file's path for *config_dir*."""
    return Path(config_dir or "config") / DRAFT_FILENAME


def _as_step(value: Any) -> int:
    """*value* as a step number in ``0..LAST_STEP``; anything else means "nothing completed yet"."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(LAST_STEP, number))


def _as_mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


class SetupDraft:
    """The tour's answers and how far it has got, as read from and written to the draft file."""

    def __init__(self, *, step: int = 0, language: str | None = None, bot_language: bool = False,
                 autoupdate: bool = True, discord: bool = True, extra: Mapping | None = None,
                 main_extra: Mapping | None = None, database: Mapping | None = None,
                 node: Mapping | None = None, servers: Mapping | None = None,
                 discord_bot: Mapping | None = None,
                 roles: Mapping | None = None, community: Mapping | None = None,
                 console: Mapping | None = None, join: Mapping | None = None,
                 review: Mapping | None = None) -> None:
        self.step = _as_step(step)
        self.language = language or None
        self.bot_language = bool(bot_language)
        self.autoupdate = bool(autoupdate)
        self.discord = bool(discord)
        #: keys a LATER step owns; kept verbatim so a save here cannot drop them
        self.extra: dict[str, Any] = dict(extra or {})
        self.main_extra: dict[str, Any] = dict(main_extra or {})
        #: step 3's answers (and its verified flag) — non-secret only; passwords never land here
        self.database: dict[str, Any] = dict(database or {})
        #: step 4's answers — this node's DCS install, listen port, static IP and DGSA opt-ins
        self.node: dict[str, Any] = dict(node or {})
        #: step 5's answers — the detected Saved Games instances and one row per instance
        self.servers: dict[str, Any] = dict(servers or {})
        #: step 6's answers — the guild id, the owner and the member-count decision (never the token)
        self.discord_bot: dict[str, Any] = dict(discord_bot or {})
        #: step 7's answers — the role mapping, the audit channel and the admin-channel choice
        self.roles: dict[str, Any] = dict(roles or {})
        #: step 6' answers — the DCS group's name and the per-role UCID lists (the no-Discord path)
        self.community: dict[str, Any] = dict(community or {})
        #: the console account — the username and the password HASH, never the password itself
        self.console: dict[str, Any] = dict(console or {})
        #: the joining state's own answer — the add/rename route (see
        #: :mod:`services.webservice.setup.join`); absent in a first-run tour
        self.join: dict[str, Any] = dict(join or {})
        #: the Review step's last write attempt — per-target outcomes, so a half-written
        #: configuration survives a reload as the same honest list
        self.review: dict[str, Any] = dict(review or {})

    # ----------------------------------------------------------------------------- (de)serialise

    def as_document(self) -> dict[str, Any]:
        """The file body: this module's answers, plus every key a later step left behind.

        ``main.language`` is present only when the operator asked for the bot to be set to their
        language too; that is exactly the installer's own rule (``install.py`` passes ``language`` to
        ``build_main`` only when the bot-language answer was yes).
        """
        main: dict[str, Any] = dict(self.main_extra)
        main["autoupdate"] = bool(self.autoupdate)
        if self.bot_language and self.language:
            main["language"] = self.language
        else:
            main.pop("language", None)
        document: dict[str, Any] = dict(self.extra)
        document["step"] = int(self.step)
        document["discord"] = bool(self.discord)
        if self.language:
            document["language"] = self.language
        else:
            document.pop("language", None)
        document["main"] = main
        document["database"] = dict(self.database)
        document["node"] = dict(self.node)
        document["servers"] = dict(self.servers)
        document["discord_bot"] = dict(self.discord_bot)
        document["roles"] = dict(self.roles)
        document["community"] = dict(self.community)
        document["console"] = dict(self.console)
        document["join"] = dict(self.join)
        document["review"] = dict(self.review)
        return document

    @classmethod
    def from_document(cls, data: Any) -> "SetupDraft":
        """Build a draft from a file body, keeping every key this module does not own."""
        if not isinstance(data, Mapping):
            return cls()
        main = _as_mapping(data.get("main"))
        language = data.get("language")
        return cls(
            step=_as_step(data.get("step")),
            language=str(language) if isinstance(language, str) and language else None,
            bot_language=bool(main.get("language")),
            autoupdate=bool(main.get("autoupdate", True)),
            discord=bool(data.get("discord", True)),
            extra={key: value for key, value in data.items() if key not in _OWNED_KEYS},
            main_extra={key: value for key, value in main.items() if key not in _OWNED_MAIN_KEYS},
            database=_as_mapping(data.get("database")),
            node=_as_mapping(data.get("node")),
            servers=_as_mapping(data.get("servers")),
            discord_bot=_as_mapping(data.get("discord_bot")),
            roles=_as_mapping(data.get("roles")),
            community=_as_mapping(data.get("community")),
            console=_as_mapping(data.get("console")),
            join=_as_mapping(data.get("join")),
            review=_as_mapping(data.get("review")),
        )

    # ---------------------------------------------------------------------------------- reading

    @classmethod
    def load(cls, config_dir) -> "SetupDraft":
        """The draft on disk, or a fresh one. An unreadable draft is reported and treated as empty."""
        from core.utils import configwrite

        path = draft_path(config_dir)
        try:
            data = configwrite.load_config_file(path)
        except configwrite.ConfigWriteError as ex:
            log.warning("Setup tour: the draft at '%s' could not be read (%s); starting fresh.",
                        path, ex)
            return cls()
        return cls.from_document(data)

    # ---------------------------------------------------------------------------------- writing

    def save(self, config_dir) -> None:
        """Write the draft through the ONE config-write primitive.

        The config directory is created when it is missing: on a first-boot install it may not exist
        yet, and the primitive (rightly) refuses to write into a directory that is not there.

        :raises ConfigWriteError: the draft could not be written. A filesystem refusal (the
            directory cannot be created, the swap cannot be made) is raised as the primitive's own
            error class, so a caller has ONE failure type to classify and never sees a raw OSError.
        """
        from core.utils import configwrite

        path = draft_path(config_dir)
        try:
            os.makedirs(str(path.parent), exist_ok=True)
            configwrite.save_config_file(path, self.as_document(), schemas=None)
        except OSError as ex:
            raise configwrite.ConfigWriteError(
                f"Configuration file could not be written ({ex}): {path}", path=path) from ex

    # -------------------------------------------------------------------------------- the steps

    @property
    def finished(self) -> bool:
        """Whether every step is behind us."""
        return self.step >= LAST_STEP
