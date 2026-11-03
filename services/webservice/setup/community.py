"""Step 6' of the tour — "Your community": the no-Discord path.

It REPLACES steps 6 and 7 in the rail when the operator answered "no" to Discord support (the agreed
layout): one screen with the DCS group's name and the users holding the ``Admin`` and ``DCS Admin``
roles, as one UCID per field (a comma-separated entry is still read). Its answers are the installer's
own inputs for the no-Discord path — ``main.yaml``'s ``guild_name`` and ``services/bot.yaml``'s
``no_discord`` role mapping — turned into the two shapes the installer writes through the shared
:func:`core.utils.setup_config` builders.

A UCID is the 32-character DCS player id, not a Discord role: every entry is lowercased and every
entry is checked with :func:`core.utils.helper.is_ucid` before the step may advance. An empty list is
accepted — a role with no users grants nothing, which the bot tolerates.

THE DISCORD ANSWERS ARE KEPT, NOT DISCARDED. This block is separate from
:mod:`services.webservice.setup.discord`'s, so flipping Discord off and back on leaves both sides'
answers where they were: the block a step left behind is still in the draft and re-renders when it
returns to the rail.

Nothing here writes a file: the tour's draft is the destination, and the Review step builds the
configuration.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any, Mapping

from core.utils import setup_config
from core.utils.helper import is_ucid

__all__ = [
    "DEFAULT_ROLES",
    "CommunityAnswers",
    "answers_from_mapping",
    "non_secret_document",
    "resolve_community",
    "main_block",
    "roles_block",
    "bot_block",
]

log = logging.getLogger(__name__)

#: the internal roles the no-Discord install defines (the installer's ``DEFAULT_NO_DISCORD_ROLES``)
DEFAULT_ROLES = setup_config.DEFAULT_NO_DISCORD_ROLES


# ------------------------------------------------------------------------------------ the answers

@dataclass
class CommunityAnswers:
    """Step 6' - the no-Discord community screen: the DCS group's name and one UCID list per
    internal role."""

    guild_name: str = ""
    users: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {name: () for name in DEFAULT_ROLES})


# ------------------------------------------------------------------------------------ the parsing

def _entries(values: Mapping, key: str) -> list:
    """Every submitted value for *key* (a repeated field), tolerating a plain mapping."""
    getlist: Any = getattr(values, "getlist", None)
    if callable(getlist):
        return list(getlist(key))
    value = values.get(key)
    return [] if value is None else [value]


def _split_users(entries) -> tuple[str, ...]:
    """The UCIDs in one row: every entry split on commas, stripped, lowercased, empties and duplicates
    dropped. Order is the operator's, so the screen re-renders the fields as they were typed."""
    values: list[str] = []
    for entry in entries:
        for item in str(entry or "").split(","):
            item = item.strip().lower()
            if item and item not in values:
                values.append(item)
    return tuple(values)


def _unusable_ucid(users: Mapping[str, tuple[str, ...]]) -> tuple[str, str] | None:
    """The first UCID no role can hold: ``(role name, the offending value)``, or None.

    :func:`core.utils.helper.is_ucid` is the project's validator — 32 characters, letters and digits,
    already lowercase — so the caller lowercases first and this only classifies.
    """
    for name in DEFAULT_ROLES:
        for value in users.get(name, ()):
            if not is_ucid(value):
                return name, value
    return None


# ------------------------------------------------------------------------------------ the reading

def answers_from_mapping(data: Mapping | None) -> CommunityAnswers:
    """Build the no-Discord step's answers from the draft's ``community`` block.

    An entry is a list of UCIDs; a single comma-joined STRING is read the same way, so a draft written
    before the rows became one value per field still loads and re-renders as separate fields.
    """
    data = data if isinstance(data, Mapping) else {}
    stored = data.get("users")
    stored = stored if isinstance(stored, Mapping) else {}
    users: dict[str, tuple[str, ...]] = {}
    for name in DEFAULT_ROLES:
        values = stored.get(name)
        if isinstance(values, str):
            values = [values]
        users[name] = _split_users(values) if isinstance(values, (list, tuple)) else ()
    return CommunityAnswers(guild_name=str(data.get("guild_name") or "").strip(), users=users)


def non_secret_document(answers: CommunityAnswers) -> dict:
    """The draft-writable part of the no-Discord step's answers (it holds no secret)."""
    return {
        "guild_name": answers.guild_name,
        "users": {name: list(answers.users.get(name, ())) for name in DEFAULT_ROLES},
    }


# ----------------------------------------------------------------------------------- the resolving

def resolve_community(values: Mapping, previous: CommunityAnswers, *, validate: bool
                       ) -> tuple[CommunityAnswers | None, tuple[str, dict] | None]:
    """Turn the submitted step 6' form into its answers.

    Every field is one UCID (a value carrying commas is still split, which is the installer's own
    input shape and the one that works with scripting off); each is stripped, lowercased and deduped.
    With ``validate`` set an empty group name, or a UCID the project's validator refuses, returns
    ``(None, (code, data))`` — the classified failure that keeps the step open, naming the role and
    the offending value for the second one. The UCID lists may be empty: a role with no users grants
    nothing, which the bot already tolerates, and the installer asks for them the same way.
    """
    users = {name: _split_users(_entries(values, f"user_{index}"))
             for index, name in enumerate(DEFAULT_ROLES)}
    answers = CommunityAnswers(guild_name=str(values.get("guild_name") or "").strip(), users=users)
    if validate:
        if not answers.guild_name:
            return None, ("community_name_required", {})
        unusable = _unusable_ucid(users)
        if unusable is not None:
            role, value = unusable
            return None, ("community_ucid_invalid", {"role": role, "value": value})
    return answers, None


# ------------------------------------------------------------------------------- the config shapes

def main_block(answers: CommunityAnswers) -> dict:
    """The part of ``main.yaml`` step 6' owns: the DCS group's name.

    The placeholder guild id a no-Discord install needs is drawn by
    :func:`core.utils.setup_config.build_no_discord_main`, which the Review step calls; drawing one
    here would mint a second, discarded value.
    """
    return {"guild_name": answers.guild_name}


def roles_block(answers: CommunityAnswers) -> dict[str, list]:
    """The ``bot.yaml`` role mapping for a no-Discord install: role name -> its UCIDs."""
    return {name: list(answers.users.get(name, ())) for name in DEFAULT_ROLES}


def bot_block(answers: CommunityAnswers) -> dict:
    """The ``services/bot.yaml`` body, built by the installer's own builder."""
    return setup_config.build_no_discord_bot(roles_block(answers))
