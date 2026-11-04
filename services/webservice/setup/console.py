"""The tour's break-glass console account: one local username and password for the console itself.

WHY THE TOUR COLLECTS IT. The install the tour writes has no identity backend at all, so the first
visit to the console would land on the login page's "no sign-in method is configured" card. The
tour therefore configures the console's own **break-glass** credential — the local Admin account that
signs in through ``/auth/login`` on the day Discord is unreachable (``auth.breakglass`` in
``config/services/webservice.yaml``). Discord login can be added later; this one always exists.

THE HASH IS THE CONSOLE'S OWN. The password is hashed with
:func:`services.webservice.auth.local.hash_password` — the exact PBKDF2-HMAC-SHA256 scheme, iteration
default and encoded form ``auth.local.users[].password_hash`` is generated with today (the "generate
one with ``python -m services.webservice.auth.local --hash``" value). No new hash is invented here,
and :func:`services.webservice.auth.breakglass.breakglass_backend` verifies it with
:func:`~services.webservice.auth.local.verify_password`.

WHAT IS STORED WHERE. Only the HASH reaches the tour's draft (:func:`non_secret_document`), which is
what makes the account survive a console restart and lets the Review step write the file. The
PLAINTEXT password is never stored at all — it arrives in a form field and is hashed in the same
request, so there is nothing anywhere (no draft, no cookie, no process store, no log line) for a
reader of the disk to find.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Mapping

from ..auth import local as local_auth

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "ConsoleAnswers",
    "answers_from_mapping",
    "non_secret_document",
    "resolve_console",
    "hash_password",
    "account_block",
    "discord_block",
]

log = logging.getLogger(__name__)

#: the console's own password bound, reused so the tour refuses exactly what a login would refuse
MAX_PASSWORD_LENGTH = local_auth.MAX_PASSWORD_LENGTH


# ------------------------------------------------------------------------------------ the answers

@dataclass
class ConsoleAnswers:
    """The Web-console step's answers: whether the console runs at all, the address it is reached at,
    the account that opens it, and the STORED HASH of that account's password."""

    #: whether the admin console is enabled at all (the step's own switch)
    enabled: bool = True
    #: the public origin the console is reachable at (what OAuth redirect URIs are built from)
    public_base_url: str = ""
    username: str = ""
    password_hash: str = ""


# ------------------------------------------------------------------------------------ the reading

def answers_from_mapping(data: Mapping | None) -> ConsoleAnswers:
    """Build the account from the draft's ``console`` block (the hash, never a password)."""
    data = data if isinstance(data, Mapping) else {}
    return ConsoleAnswers(enabled=bool(data.get("enabled", True)),
                          public_base_url=str(data.get("public_base_url") or "").strip(),
                          username=str(data.get("username") or "").strip(),
                          password_hash=str(data.get("password_hash") or "").strip())


def non_secret_document(answers: ConsoleAnswers) -> dict:
    """The draft-writable part of the account: the switch, the address, the username and the hash."""
    return {"enabled": bool(answers.enabled),
            "public_base_url": answers.public_base_url,
            "username": answers.username, "password_hash": answers.password_hash}


def hash_password(password: str) -> str:
    """The console's own password hash (see the module docstring). Raises ``ValueError``.

    A thin wrapper, not a second implementation: the bound and the encoded form are the ones
    :func:`services.webservice.auth.local.hash_password` already enforces, so a tour-written hash is
    accepted by the login that reads it.
    """
    return local_auth.hash_password(password)


# ----------------------------------------------------------------------------------- the resolving

def resolve_console(values: Mapping, previous: ConsoleAnswers, *, validate: bool,
                    default_base_url: str = "") -> tuple[ConsoleAnswers | None, tuple[str, dict] | None]:
    """Turn the submitted console-step fields into the answers.

    The step's SWITCH is read first: with the console off nothing else is asked and the username and
    password are NOT required (a console-less install must not be made to invent an account). A typed
    password is hashed HERE and only the hash is kept; an empty field keeps the hash already stored,
    and an empty USERNAME keeps the one already stored too — so re-opening the step (or submitting a
    form that does not carry the field) never wipes an account. The public base URL falls back to what
    was stored, then to *default_base_url* (the address the step prefilled it with). With ``validate``
    set, a missing username or password returns the classified failure that keeps the step open.
    """
    enabled = values.get("console_enabled") is not None
    base_url = (str(values.get("console_public_base_url") or "").strip()
                or previous.public_base_url or default_base_url)
    username = str(values.get("console_username") or "").strip() or previous.username
    password = values.get("console_password")
    password = password if isinstance(password, str) else ""
    password_hash = previous.password_hash
    if password:
        try:
            password_hash = hash_password(password)
        except ValueError:
            return None, ("console_password_invalid", {"max": MAX_PASSWORD_LENGTH})
    answers = ConsoleAnswers(enabled=enabled, public_base_url=base_url, username=username,
                             password_hash=password_hash)
    if not validate or not enabled:
        return answers, None
    if not answers.username:
        return None, ("console_username_required", {})
    if not answers.password_hash:
        return None, ("console_password_required", {})
    return answers, None


# ------------------------------------------------------------------------------- the config shape

def account_block(answers: ConsoleAnswers) -> dict:
    """The ``auth.breakglass`` block for ``config/services/webservice.yaml``, or ``{}`` when unset.

    Enabling it is part of the account: the backend is refused unless ``enabled`` is literally true,
    so a block written without it would be an account nobody can use. It is written whenever the
    console is enabled — NEVER \"Discord only\": the account is the door that exists on the day
    Discord is the thing that broke.
    """
    if not answers.enabled or not answers.username or not answers.password_hash:
        return {}
    return {"enabled": True, "username": answers.username, "password_hash": answers.password_hash}


def discord_block(answers: ConsoleAnswers, client_id: int, *, secret_held: bool) -> dict:
    """The ``auth.discord`` block for ``config/services/webservice.yaml``, or ``{}``.

    Written only when the console is enabled, the bot application id is known and a client secret is
    actually held in this process — the store key the block names is what the OAuth backend reads, and
    an enabled backend with a store key the process cannot write is refused at boot. The secret itself
    is never named here: ``client_secret_key`` is a KEY in ``config/.secret/``.
    """
    from . import discord as discord_step

    if not answers.enabled or not client_id or not secret_held:
        return {}
    return {"enabled": True, "client_id": int(client_id),
            "client_secret_key": discord_step.CLIENT_SECRET_KEY}
