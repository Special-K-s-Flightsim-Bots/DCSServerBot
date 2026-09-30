"""The Discord OAuth identity backend — the second door into the same console.

WHAT THE FLOW IS (and what it deliberately is not):

1. ``GET /auth/discord`` (:data:`START_PATH`) mints a single-use ``state`` into the session and
   redirects to Discord's authorize endpoint with ``identify`` **plus** ``guilds.members.read``
   (:data:`SCOPE`). ``identify`` is what the flow needs to learn WHO signed in; the second scope
   exists for :data:`GUILD_MEMBER_URL` only — the user-side lookup that resolves ONE membership with
   the user's own token when the bot cannot read the guild's members (see below). It is NOT a second
   path to the role model: that lookup returns role IDs and they are expanded through the SAME
   ``bot.roles`` map, so the console and the bot's commands still cannot disagree about who is an
   Admin.
2. ``GET /auth/discord/callback`` (:data:`CALLBACK_PATH`) consumes the state, exchanges the code
   for a token (``aiohttp``, with a timeout), calls ``/users/@me`` for the Discord id, and then
   resolves the MEMBER THROUGH THE RUNNING BOT: the bot is already in the guild and already owns
   ``bot.roles``, so the role model has ONE source of truth for both the console and the commands.
3. The member's Discord roles are expanded into the bot's role NAMES through ``bot.roles`` — the
   DISCORD meaning of ``bot.yaml -> roles:``, a role NAME mapped to the Discord roles that grant
   it (``services/bot/dcsserverbot.py:114-125``, ``samples/services/bot.yaml``). Those names feed
   the SAME capability map the local backend feeds (:mod:`services.webservice.permissions`), so
   "offered ⊆ authorised" is asserted per backend against one declaration.

HOW THE MEMBER IS RESOLVED — the intent problem this module has to survive:

discord.py fills its member cache from the gateway, which requires the **Server Members Intent**.
With ``privileged_intents: false`` in ``config/services/bot.yaml`` — what the repo itself recommends
for guilds over 10.000 members — the cache holds NOBODY, so a cache-only lookup resolves every user,
the guild owner included, to ``None``.

* The LOGIN path (:meth:`DiscordOAuthBackend.complete_login`) resolves in three steps and logs WHICH
  one answered: the live cache, then ``Guild.fetch_member`` (ONE member over REST, which does not
  need the privileged intent), then :data:`GUILD_MEMBER_URL` with the token just obtained, which
  answers honestly even when the bot's own lookup is refused (404 = not a member of that guild).
* The PER-REQUEST path (:meth:`DiscordOAuthBackend.authenticate`) is sync and must stay cheap: the
  live cache, then a short-TTL in-memory resolution cache (``auth.discord.member_ttl``, default
  :data:`DEFAULT_MEMBER_TTL` seconds) that the login SEEDS and that a background REST fetch refreshes
  as soon as an entry is used after its TTL. The value served is therefore at most one TTL old plus
  the duration of one refresh, and a member who loses a role or leaves the guild loses the capability
  within that window — not at cookie expiry. A subject with NO entry is refused (fail closed) while
  the refresh runs, and a refresh that FAILS drops the entry, so a stale answer is never kept alive
  by a failure. The cache lives in memory in this process: a webservice restart forgets every
  resolution, so an existing session is refused until the user signs in again (which re-seeds it).
  With the members intent ON none of this applies: the live cache is authoritative, a
  role change is effective on the very next request, and no extra API call happens.
* DELIBERATE DIVERGENCE from the rest of the bot, stated here on purpose: the bot's own player
  auto-matching simply DECLINES when there is no member list (``services/bot/dcsserverbot.py:643``
  returns ``None``). The console must not copy that — refusing on an empty cache would lock the
  operator out of the admin UI — so this path DEGRADES (one REST lookup per login, at most one
  refresh per TTL per subject) instead of refusing. The corollary is the wording rule: "not a member
  of the bot's guild" may only be claimed when the bot can actually see members
  (:func:`members_intent_available`); otherwise the refusal names the intent/cache problem.
* A Discord REST failure never becomes a 500 and never becomes an open door: the subject is refused
  and the failure is a log line.

WHAT MUST NOT BE REUSED — verified in the phase-1 review, §D.3, and kept as hard rules here:

* :func:`core.utils.discord.check_roles` returns ``False`` for every headless member (its first
  guard is ``isinstance(member, discord.Member)`` and ``DummyMember`` is a plain class);
* :func:`core.utils.discord.app_has_role` indexes ``client.roles[role]`` (``DummyBot.roles`` has
  no ``"DCS"`` key) and its owner bypass needs an interaction — there is none in a web request;
* ``DummyGuild`` reads ``config/services/bot.yaml`` off a relative path;
* any second role config: the console reads the role model the bot process is already running.

So the role expansion is done here, with ``.get(role, [])``-style tolerance everywhere (a grant
list that is absent or empty grants that role to nobody), and ``current_bot()`` is resolved PER
REQUEST — ``ServiceRegistry.get(BotService).bot`` is ``None`` early, after a takeover, and on a
headless install (``services/bot/service.py:211``), so caching it at startup would be wrong.

A signed-in user who is NOT a member of the bot's guild is REFUSED with the reason logged (they
were never part of the role model); a member with no mapped role signs in with an EMPTY role set
and the ordinary deny-by-default rules apply. ``bot is None`` is the same refusal, never a crash
and never a silent downgrade to "no roles".

CONFIG — ``auth.discord`` in ``config/services/webservice.yaml``:

============================ ========= =======================================================
key                          default   meaning
============================ ========= =======================================================
``enabled``                  ``false`` nothing is enabled unless this is exactly ``true``
``client_id``                —         the Discord application's client id
``client_secret``            —         the client secret, INLINE
``client_secret_key``        —         the name of a key in the repo's secret store,
                                       ``config/.secret/<key>.pkl`` (``utils.get_password``);
                                       **when set it WINS** over ``client_secret``
``member_ttl``               ``60``    seconds a per-request member resolution may be served from
                                       memory when the bot has no member list (intents off);
                                       clamped to ``1..3600`` with a warning
============================ ========= =======================================================

SECRET PRECEDENCE, pinned by the lead and pinned by tests: ``enabled`` gates everything;
``client_id`` is required when enabled; the secret comes from ``client_secret_key`` **if that key
is set**, otherwise from the inline ``client_secret``. If neither yields a secret, or a named store
key does not exist, the install is refused LOUDLY at startup (``ValueError``) rather than starting
a login that cannot complete — an enabled backend that cannot work is discovered in the login
attempt, which is exactly the wrong moment. The secret-store helper is imported INSIDE
:func:`_secret_from_store` because the store is only touched when a secret is actually read — NOT
to keep this module light: it reaches the bot's ``core`` at import time through ``..scope`` (the
shared ``managed_by`` rule), like every module of the ``auth`` package.

SESSION SAFETY: the ``state`` is bound to this browser's signed session, compared in constant time
and CONSUMED on use (popped before the comparison, so missing, foreign, wrong and replayed are all
refusals and a failure burns the nonce). Nothing about the pre-login session survives into the
authenticated one (``session.clear()`` before the reference is written), and the cookie never
carries a role or a token: it holds ``{"backend": "discord", "subject": "<discord id>"}`` only,
resolved against the live bot on every later request. The access token lives in ONE request — the
callback — and is used for ``/users/@me`` and (only if the bot's own lookup fails) for
:data:`GUILD_MEMBER_URL`; it is never stored, never logged and never rendered.

REDIRECTS: the redirect URI is the configured ``auth.public_base_url`` plus
:data:`CALLBACK_PATH` — NEVER derived from the request. A ``Host`` or ``X-Forwarded-*`` header must
not be able to move it: behind a proxy that silently flips ``https`` to ``http`` (or lands on
another origin whose cookie is not sent) the only symptom is "login fails, works on retry".

SECRETS IN LOGS: the authorization code, the access token and the client secret are never logged
and never rendered. Errors carry the HTTP status or the failure class, and the page a visitor sees
is one generic sentence.

BOUNDED ANSWERS: every call goes through :func:`_request`, which bounds BOTH the round-trip
(``TIMEOUT_SECONDS``) and the answer (``MAX_RESPONSE_BYTES``). The body is read through a capped
read, so a wrong or hostile endpoint — or a hijacked DNS answer — cannot pull an unbounded body
into the bot process: an oversized answer is a failure whose log line names the limit and never the
body.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import (AuthBackend, Identity, clear_session_identity, safe_avatar_url,
               session_identity_ref, set_session_identity)
from .. import permissions
from ..scope import Scope, member_scope_tokens

__all__ = [
    "BACKEND_NAME", "START_PATH", "CALLBACK_PATH", "AUTHORIZE_URL", "TOKEN_URL", "USER_URL",
    "GUILD_MEMBER_URL", "SCOPE", "STATE_SESSION_KEY", "NEXT_SESSION_KEY", "TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES", "AVATAR_SIZE",
    "SITE_ROOT", "GENERIC_MESSAGE", "DISABLED_MESSAGE", "CANCELLED_MESSAGE", "SECRET_STORE_DIR",
    "DEFAULT_MEMBER_TTL", "MIN_MEMBER_TTL", "MAX_MEMBER_TTL", "NO_MEMBERS_INTENT_REASON",
    "LOOKUP_LABELS", "DiscordAuthError", "DiscordSettings", "DiscordOAuthBackend",
    "DiscordSecretReader", "MemberLookup", "discord_backend", "parse_discord_config",
    "role_names_for_member", "resolve_member", "members_intent_available",
    "member_refusal_reason", "member_presentation", "member_roles_snapshot", "fetch_member_rest",
    "fetch_user_member", "resolve_login_member", "current_bot", "begin_flow", "match_flow",
    "finish_flow", "discard_flow", "exchange_code", "fetch_user", "capabilities", "add_routes",
    "error_page",
]

log = logging.getLogger(__name__)

#: short, stable backend name; also what the session reference stores and the log names
BACKEND_NAME = "discord"

#: absolute constants, shared by the routes, the developer-portal redirect URI and the tests.
#: Never a config prefix: a prefix from a user-editable file would move the callback out from
#: under the redirect URI registered with Discord, and nothing would warn (§C.1).
START_PATH = "/auth/discord"
CALLBACK_PATH = "/auth/discord/callback"

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
USER_URL = "https://discord.com/api/users/@me"

#: ONE membership, read as the USER (their own token) instead of as the bot. The fallback for a bot
#: that cannot read its guild's members (no Server Members Intent). 404 = not a member of that guild.
GUILD_MEMBER_URL = "https://discord.com/api/users/@me/guilds/{guild_id}/member"

#: ``identify`` is what the flow needs to learn WHO signed in; ``guilds.members.read`` exists for
#: :data:`GUILD_MEMBER_URL` only — the user-side fallback. It is not a second path to the role
#: model: the role IDs it returns are expanded through the SAME ``bot.roles`` map.
SCOPE = "identify guilds.members.read"

#: how long a per-request member resolution may be served from memory when the bot has no member
#: list (intents off). A value is never served older than this, so a removed member/role loses the
#: capability within the TTL — not at cookie expiry. Clamped to MIN..MAX with a warning.
DEFAULT_MEMBER_TTL = 60
MIN_MEMBER_TTL = 1
MAX_MEMBER_TTL = 3600

#: THE RETRY SCHEDULE of a member resolution the bot could not ANSWER. A failure that does not
#: answer the membership question (no member list, a REST error, the bot not ready, a rate limit, a
#: timeout) is a CONDITION, not a fact, so it is re-asked in the background: the first retry waits
#: :data:`MEMBER_RETRY_MIN` seconds and each further one doubles up to :data:`MEMBER_RETRY_MAX` —
#: a bot that is restarting is re-asked within seconds, while a bot that is down for an hour is not
#: asked several hundred times.
MEMBER_RETRY_MIN = 2.0
MEMBER_RETRY_MAX = 300.0

#: the seconds a RETRYABLE refusal carries in ``Retry-After``. The identity is not refused (403) —
#: it could not be VERIFIED, which is a thing that passes, so the client is told to retry.
MEMBER_RETRY_AFTER = 5

#: the truthful refusal: the bot cannot see members, so "not a member" is not something we know.
#: It names the way out, because the operator is the one who can enable the intent.
NO_MEMBERS_INTENT_REASON = (
    "cannot resolve guild members: the bot runs without the Server Members Intent "
    "(privileged_intents: false in config/services/bot.yaml), so Discord exposes no member list to "
    "it and its member cache is empty; neither the bot-side single-member lookup nor the user-side "
    "members.read lookup could answer. Enable the Server Members Intent (privileged_intents: true "
    "in config/services/bot.yaml, and the intent ticked in the Discord Developer Portal) to give "
    "the bot a member list, or check the lookup failures logged above.")

#: the resolution path, as it is logged once per login (and read by the tests)
LOOKUP_LABELS = {
    "cache": "the bot member cache",
    "bot": "the bot REST lookup",
    "user": "the user-side members.read lookup",
}

#: the ONE summary line a standing member-resolution refusal emits once its interval elapses: the
#: count and when the condition started, and NEVER a subject id (the repeating line is what an
#: operator scans, and a per-request id is noise).
_SUPPRESSED_MEMBER_REFUSALS = (
    lambda count, since: (
        f"auth: {count} similar Discord member refusals suppressed since {since} "
        f"(the first occurrence above carries the full reason)"))

#: session keys. One flow exists in this package (the login door), so one state key — a second
#: flow (an appeal nonce, say) would need its own key, or one flow's callback could consume the
#: other's nonce (the bug recorded in DCSServerBotCloud/app/oauth_state.py).
STATE_SESSION_KEY = "webui_oauth_state"
NEXT_SESSION_KEY = "webui_oauth_next"

#: bounds the Discord round-trip; a hung identity provider must not park a worker forever
TIMEOUT_SECONDS = 10

#: bounds the ANSWER, not just the time it takes: the user object and the token payload are a few
#: hundred bytes, so anything past this cap is not a Discord login answer at all (a wrong endpoint,
#: a hostile response, a hijacked DNS answer). The body is read through a capped read, so an
#: oversized answer never lands in memory in full — it is a failure, logged with the limit and never
#: with the body.
MAX_RESPONSE_BYTES = 64 * 1024

#: the avatar rendition the shell asks Discord's CDN for. The chip draws a 30px circle, so the
#: full-size asset would be a needless download on every page load; 64px covers a 2x display.
AVATAR_SIZE = 64

#: where the login lands when the flow carried no usable target (never off-site — see _safe_next)
SITE_ROOT = "/"

#: what a visitor is told. One generic sentence: the REASON (not a member / no token / Discord
#: error) is a log line an operator can read, and never something a page anyone can type at
#: discloses.
GENERIC_MESSAGE = ("Sign-in with Discord could not be completed. Please try again, or sign in "
                   "with your username and password.")
DISABLED_MESSAGE = "Sign-in with Discord is not enabled on this console."
CANCELLED_MESSAGE = "Sign-in with Discord was cancelled."

#: the label and look of the login page's second door (read by templates/login.html)
CONTROL_LABEL = "Sign in with Discord"

#: the repo's pickle secret store, relative to the config dir (``config/.secret/<key>.pkl``)
SECRET_STORE_DIR = ".secret"

#: the reader a deployment uses: ``get_password(key, config_dir)`` from ``core.utils.os``
DiscordSecretReader = Callable[[str, "str | Path"], str]


class DiscordAuthError(RuntimeError):
    """A refusal of the Discord login. Its MESSAGE is safe to log and is never rendered."""


# --------------------------------------------------------------------------- the bot & the roles

def current_bot():
    """``ServiceRegistry.get(BotService).bot`` — resolved PER REQUEST, never cached.

    The bot object is ``None`` early in startup, after a master/agent takeover and on a headless
    install, so a cached copy would hold a stale object (or ``None`` forever). The imports are
    inside the function because they resolve a RUNTIME object — the registry's ``BotService`` only
    exists while a request is being served. This module DOES reach the bot's ``core`` at import
    time (``..scope`` imports the shared ``managed_by`` rule from ``core.utils.discord``), which is
    deliberate: the console runs in the bot's own environment.
    """
    from core import ServiceRegistry
    from services.bot import BotService

    service = ServiceRegistry.get(BotService)
    return getattr(service, "bot", None) if service is not None else None


def _subject_key(subject):
    """A Discord id as the guild's member lookup wants it: an ``int`` when the subject is numeric.

    ``discord.Guild.get_member`` is a dict lookup keyed by user id, so a string never matches;
    ``DummyGuild.get_member`` is keyed by MEMBER NAME, which is precisely why a Discord id can
    never resolve there — and why a headless install refuses this path instead of inventing roles.
    """
    text = str(subject or "").strip()
    return int(text) if text.isdigit() else text


def resolve_member(bot, subject):
    """The member behind *subject* in any guild the bot is in, or ``None``.

    A cache lookup only (``guild.get_member``): no Discord HTTP call happens on a request path.
    """
    key = _subject_key(subject)
    if not key:
        return None
    for guild in getattr(bot, "guilds", None) or ():
        getter = getattr(guild, "get_member", None)
        if getter is None:  # pragma: no cover - every guild object has one
            continue
        try:
            member = getter(key)
        except Exception:  # pragma: no cover - a lookup must never break a login with a 500
            log.warning("auth: the guild member lookup failed for %r", key, exc_info=True)
            continue
        if member is not None:
            return member
    return None


def member_presentation(member) -> tuple[str, str]:
    """``(display name, avatar URL)`` for the member the roles were resolved from.

    NO DISCORD CALL: everything read here lives on the member object, which the request already
    holds for the role expansion — that is the whole reason the console can show a name and a face
    without a second lookup.

    * the DISPLAY name (what Discord itself shows: the guild nickname when there is one) is
      preferred over the global username, with ``global_name``/``name``/``username`` as fallbacks so
      a snapshot, a REST payload or a test double all answer something;
    * the avatar comes from ``display_avatar`` — Discord substitutes a DEFAULT avatar, so it is
      always present for a real member — asked for at :data:`AVATAR_SIZE` (the CDN takes the size as
      a url parameter, so the small rendition is what gets downloaded). A member object that somehow
      has no asset still answers its ``avatar_url`` when it carries one, and ``""`` otherwise (the
      shell then draws the initials chip).

    Both values are read defensively and are treated as untrusted by the caller.
    """
    display_name = ""
    for attribute in ("display_name", "global_name", "name", "username"):
        value = getattr(member, attribute, None)
        if isinstance(value, str) and value.strip():
            display_name = value.strip()
            break

    avatar = _asset_url(getattr(member, "display_avatar", None))
    if not avatar:
        raw = getattr(member, "avatar_url", None)
        avatar = raw.strip() if isinstance(raw, str) else ""
    return display_name, avatar


def _asset_url(asset) -> str:
    """The URL of a ``discord.Asset`` at :data:`AVATAR_SIZE`, or ``""``.

    ``with_size`` is the Asset API that appends the CDN's size parameter; a snapshot or a double
    that has no such method is used as-is, and anything without a ``url`` answers ``""``.
    """
    if asset is None:
        return ""
    sized = getattr(asset, "with_size", None)
    if callable(sized):
        try:
            asset = sized(AVATAR_SIZE)
        except Exception:  # pragma: no cover - a weird asset must not break a page render
            log.debug("auth: could not size a Discord asset - using it as given", exc_info=True)
    url = getattr(asset, "url", None)
    return url.strip() if isinstance(url, str) else ""


def role_names_for_member(member, role_map) -> set[str]:
    """The bot's role NAMES the member's Discord roles grant.

    ``role_map`` is ``bot.roles``: role NAME -> the Discord roles (names or ids) that grant it.
    Every list is read tolerantly (``granted or []``), so a missing key — ``DummyBot.roles`` has no
    ``"DCS"`` at all — grants nothing instead of raising ``KeyError`` on the one install most
    likely to be exercised by hand.

    The match is on role ID or role NAME, the same pair :func:`core.utils.discord.check_roles`
    used, re-implemented here because ``check_roles`` refuses every headless member (§D.3).
    """
    held_ids: set[int] = set()
    held_names: set[str] = set()
    for role in getattr(member, "roles", None) or ():
        role_id = getattr(role, "id", None)
        if isinstance(role_id, int) and not isinstance(role_id, bool):
            held_ids.add(role_id)
        name = getattr(role, "name", None)
        if isinstance(name, str) and name:
            held_names.add(name)

    names: set[str] = set()
    for name, granted in (role_map or {}).items():
        for grant in (granted or []):
            if isinstance(grant, bool):  # True/False are ints in Python; never a role id
                continue
            if isinstance(grant, int):
                if grant in held_ids:
                    names.add(str(name))
                    break
            elif isinstance(grant, str):
                if grant in held_names:
                    names.add(str(name))
                    break
    return names


# --------------------------------------------------------------------------- member resolution
#
# The bot's member cache is empty without the Server Members Intent, so "the member" is resolved in
# up to three steps (cache -> bot REST -> user-side REST). Everything below is module-level and
# dependency-free on purpose: the tests stub the two REST functions and nothing else, and no test
# can reach Discord.

def _now() -> float:
    """The monotonic clock the resolution TTL is measured against (a seam for the tests)."""
    return time.monotonic()


def members_intent_available(bot) -> bool:
    """Whether the bot's intents let discord.py CACHE guild members.

    Read defensively: a headless install (``DummyBot``) has no ``intents`` at all, and a bot that
    cannot report its intents cannot be claimed to see members — the safe direction, because the
    wording rule below depends on it. The real bot carries ``discord.Intents`` here, exactly what
    ``services/bot/dcsserverbot.py:63`` and ``:643`` read.
    """
    intents = getattr(bot, "intents", None)
    return bool(getattr(intents, "members", False))


def member_refusal_reason(bot, subject) -> str:
    """WHY a login/session was refused, in words that are true of the situation.

    "is not a member of the bot's guild" is claimed ONLY when the bot can actually see members (its
    cache is then authoritative, so an empty cache IS the answer). Without the intent the honest
    reason is the cache/intent one — the wording this module was fixed for, because the old message
    sent the operator hunting for a membership problem that did not exist.
    """
    if members_intent_available(bot):
        return f"Discord user {subject} is not a member of the bot's guild"
    return NO_MEMBERS_INTENT_REASON


def _guild_ids(bot) -> list[int]:
    """The ids of the guilds the bot is in (what the user-side lookup is asked about)."""
    ids = []
    for guild in getattr(bot, "guilds", None) or ():
        guild_id = getattr(guild, "id", None)
        if isinstance(guild_id, int) and not isinstance(guild_id, bool):
            ids.append(guild_id)
    return ids


def _as_int(value):
    """A Discord id as an ``int`` when it is numeric (role expansion matches ints), else unchanged."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


class _RestRole:
    """The two attributes :func:`role_names_for_member` reads off a role."""

    __slots__ = ("id", "name")

    def __init__(self, role_id=None, name=None):
        self.id = role_id
        self.name = name


class _RestMember:
    """A member as a REST lookup returns it: the roles plus the presentation the chip shows.

    Deliberately NOT a live object: what the TTL cache holds is a VALUE as of the lookup, so the
    staleness the TTL allows is the only staleness there is. The display name and the avatar travel
    with the snapshot — without them the chip would lose the name and the face on the very first
    request that is served from the cache, which is every request when the bot has no member list.
    """

    __slots__ = ("roles", "display_name", "avatar_url")

    def __init__(self, roles=(), display_name="", avatar_url=""):
        self.roles = list(roles)
        self.display_name = display_name
        self.avatar_url = avatar_url


def member_roles_snapshot(member) -> _RestMember:
    """A ``_RestMember`` copy of anything carrying ``.roles`` (a ``discord.Member``, a test double).

    Snapshotting is what makes the cache entry a value: the roles are the ones held when the lookup
    ran, and the TTL decides how long that answer may be used. The presentation is copied with them
    (see :func:`member_presentation`), so a snapshot of a snapshot is stable.
    """
    roles = []
    for role in getattr(member, "roles", None) or ():
        role_id = getattr(role, "id", None)
        name = getattr(role, "name", None)
        roles.append(_RestRole(role_id if isinstance(role_id, int)
                               and not isinstance(role_id, bool) else None,
                               name if isinstance(name, str) and name else None))
    display_name, avatar_url = member_presentation(member)
    return _RestMember(roles, display_name, avatar_url)


def _member_from_payload(payload) -> _RestMember:
    """The user-side member object -> the shape the role expansion reads (role IDs only).

    The endpoint answers role IDs (not names), so a grant written as a role NAME cannot match on
    this path — the bot-side lookup carries names, and it is tried first for exactly that reason.

    The display name is read from the payload's guild nickname (``nick``) with the user's
    ``global_name``/``username`` behind it. NO avatar on this path: the payload carries a raw avatar
    HASH rather than an Asset, so there is no URL to take and no ``with_size`` to ask — the shell
    draws the initials chip instead of this module hand-building a CDN URL of its own.
    """
    roles = payload.get("roles") if isinstance(payload, dict) else None
    entries = [payload] if isinstance(payload, dict) else []
    user = payload.get("user") if isinstance(payload, dict) else None
    if isinstance(user, dict):
        entries.append(user)
    display_name = ""
    for entry in entries:
        for key in ("nick", "display_name", "global_name", "username"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                display_name = value.strip()
                break
        if display_name:
            break
    return _RestMember([_RestRole(_as_int(role_id), None) for role_id in (roles or ())],
                       display_name)


def _is_not_found(ex) -> bool:
    """Whether a ``discord`` error means "no such member" rather than "the lookup failed".

    ``discord`` is not imported HERE (the check must work on any exception object, including the
    test doubles'), so the class is recognised by its ``status``/name — ``discord.NotFound``
    carries ``status = 404``.
    """
    return getattr(ex, "status", None) == 404 or type(ex).__name__ == "NotFound"


async def fetch_member_rest(bot, subject):
    """ONE member over REST (``Guild.fetch_member``), or ``None`` when no guild knows them.

    A single-member fetch does not need the Server Members Intent — the BULK member list does — so
    this is what makes a login work with ``privileged_intents: false``. A failure that is not
    "no such member" is RAISED (the caller falls back to the user-side lookup and, failing that,
    refuses): it must never be read as "not a member".
    """
    key = _subject_key(subject)
    if not key:
        return None
    for guild in getattr(bot, "guilds", None) or ():
        fetcher = getattr(guild, "fetch_member", None)
        if fetcher is None:  # pragma: no cover - every guild object has one
            continue
        try:
            member = await fetcher(key)
        except Exception as ex:
            if _is_not_found(ex):
                continue
            raise
        if member is not None:
            return member
    return None


async def fetch_user_member(access_token: str, guild_ids):
    """The caller's own membership, read with THEIR token (``guilds.members.read``).

    ``None`` when Discord answers 404 for every guild (not a member of any of them). Any other
    non-200 is a :class:`DiscordAuthError` — the failure class, never the token, and never the body.
    """
    for guild_id in guild_ids:
        payload = await _request("GET", GUILD_MEMBER_URL.format(guild_id=guild_id),
                                 headers={"Authorization": f"Bearer {access_token}"},
                                 not_found_ok=True)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            raise DiscordAuthError("the user-side member lookup answered a body that is not a JSON "
                                   "object")
        return _member_from_payload(payload)
    return None


@dataclass(frozen=True, slots=True)
class MemberLookup:
    """What the member resolution produced: the member (or ``None``) and WHICH path.

    ``source`` is one of:

    * ``"cache"`` / ``"bot"`` / ``"user"`` — the member, and the path that answered;
    * ``"absent"`` — the lookup ANSWERED and the answer is "no such member" (the bot's member list
      is authoritative and the person is not in it). A definitive negative;
    * ``"unknown"`` — the lookup could not ANSWER the membership question at all (no guilds yet, a
      REST failure, a token-less fallback). INDECISIVE: never read as a negative.

    The distinction is the whole point of the class: a member the bot cannot resolve right now is
    not a member who is gone, and the two must not share an outcome.
    """

    member: object | None
    source: str  # "cache" | "bot" | "user" | "absent" | "unknown"

    @property
    def definitive(self) -> bool:
        """Whether the lookup ANSWERED, and the answer was "no such member"."""
        return self.member is None and self.source == "absent"


async def resolve_login_member(bot, subject, access_token) -> MemberLookup:
    """Resolve the member: the live cache, then the bot's REST lookup, then the user's.

    Never raises for a lookup failure (it logs and moves on); the caller decides on the result. The
    ``source`` is what the login logs, so an operator learns which path their intents setting
    actually exercises.

    THE ONE CHAIN, used by the login AND by the background refresh (``DiscordOAuthBackend.
    _refresh``): a lookup that could not ANSWER (no guilds loaded yet, a REST failure, no token for
    the fallback) answers ``"unknown"`` — an indecision, never a negative. Only the bot's own
    authoritative member list answering "nobody" is ``"absent"``.
    """
    cached = resolve_member(bot, subject)
    if cached is not None:
        return MemberLookup(cached, "cache")
    if not _guild_ids(bot):
        # the bot has not loaded its guilds (a restart, a takeover): it cannot be asked at all, and
        # an unaskable bot is not a membership fact
        return MemberLookup(None, "unknown")
    try:
        member = await fetch_member_rest(bot, subject)
    except Exception as ex:
        log.warning("auth: the bot-side member lookup failed for Discord user %s (%s) - falling "
                    "back to the user-side members.read lookup", subject, type(ex).__name__)
    else:
        if member is not None:
            return MemberLookup(member, "bot")
        if members_intent_available(bot):
            # the cache is authoritative with the intent on, so an empty cache IS the answer
            return MemberLookup(None, "absent")
    if access_token:
        try:
            member = await fetch_user_member(access_token, _guild_ids(bot))
        except DiscordAuthError as ex:
            log.warning("auth: the user-side member lookup failed for Discord user %s (%s)",
                        subject, ex)
        else:
            if member is not None:
                return MemberLookup(member, "user")
    # without the member list a 404 from the bot's own lookup cannot be told apart from the intent
    # gate, so nothing here may be read as "not a member"
    return MemberLookup(None, "unknown")


# --------------------------------------------------------------------------- the state nonce

def begin_flow(request: Request, landing: str) -> str:
    """Mint a single-use ``state`` and remember where this browser asked to land."""
    state = secrets.token_urlsafe(32)
    request.session[STATE_SESSION_KEY] = state
    request.session[NEXT_SESSION_KEY] = landing
    return state


def match_flow(request: Request, state) -> bool:
    """Read-only check of the state against the session, for the cancel path (never mutates)."""
    expected = request.session.get(STATE_SESSION_KEY)
    if not isinstance(expected, str) or not expected or not isinstance(state, str) or not state:
        return False
    return hmac.compare_digest(state.encode("utf-8"), expected.encode("utf-8"))


def finish_flow(request: Request, state) -> str | None:
    """CONSUME the flow's state and return the validated landing path, or ``None`` when refused.

    Both session keys are popped BEFORE the comparison, so a missing, foreign, wrong or replayed
    state is a refusal that also burns the nonce (the design recorded in the sibling app's
    ``app/oauth_state.py``).
    """
    expected = request.session.pop(STATE_SESSION_KEY, None)
    landing = request.session.pop(NEXT_SESSION_KEY, None)
    if not isinstance(expected, str) or not expected:
        log.warning("auth: refusing a Discord callback - the session carries no OAuth state (a "
                    "foreign or expired session, or a replayed callback)")
        return None
    if not isinstance(state, str) or not state:
        log.warning("auth: refusing a Discord callback - the callback carried no state")
        return None
    if not hmac.compare_digest(state.encode("utf-8"), expected.encode("utf-8")):
        log.warning("auth: refusing a Discord callback - the state does not match this session's "
                    "(a foreign or replayed callback)")
        return None
    return landing if isinstance(landing, str) and landing else SITE_ROOT


def discard_flow(request: Request) -> None:
    """Drop the pending flow (the cancel path, after :func:`match_flow` accepted the state)."""
    request.session.pop(STATE_SESSION_KEY, None)
    request.session.pop(NEXT_SESSION_KEY, None)


# --------------------------------------------------------------------------- the HTTP calls

async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for a token. Raises :class:`DiscordAuthError` on failure.

    No log line here carries the code, the secret or the body: an error names the HTTP status.
    """
    data = {"client_id": client_id, "client_secret": client_secret,
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    payload = await _request("POST", TOKEN_URL, data=data)
    if not isinstance(payload, dict):
        raise DiscordAuthError("the token exchange answered a body that is not a JSON object")
    return payload


async def fetch_user(access_token: str) -> dict:
    """``GET /users/@me`` with the token. The token is never logged."""
    payload = await _request("GET", USER_URL, headers={"Authorization": f"Bearer {access_token}"})
    if not isinstance(payload, dict):
        raise DiscordAuthError("the user lookup answered a body that is not a JSON object")
    return payload


async def _request(method: str, url: str, *, data=None, headers=None,
                   not_found_ok: bool = False) -> dict | None:
    """One bounded request to Discord. Network and timeout failures become DiscordAuthError.

    ``not_found_ok`` returns ``None`` for an HTTP 404 instead of raising: the user-side member
    lookup reads 404 as the ANSWER ("not a member of that guild"), everything else as a failure.

    The BODY is bounded too (``MAX_RESPONSE_BYTES``): the answer is read through a capped read
    instead of ``response.json()``, which would pull whatever the endpoint sends into memory first.
    A wrong or hostile endpoint — or a hijacked DNS answer — must not be able to balloon the bot
    process; an oversized answer is a FAILURE, and the body (or a partial version of it) is neither
    parsed nor logged.
    """
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, data=data, headers=request_headers) as response:
                if response.status == 404 and not_found_ok:
                    return None
                if response.status != 200:
                    raise DiscordAuthError(f"Discord answered HTTP {response.status}")
                body = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    # one byte past the cap is all that is read, so the size is known without the
                    # body ever being held in full; the limit is named, the body never is
                    log.warning("auth: refusing an oversized Discord answer - the body is larger "
                                "than the %d-byte cap for an OAuth JSON response; the body itself "
                                "is neither used nor logged", MAX_RESPONSE_BYTES)
                    raise DiscordAuthError(f"Discord answered a body over the "
                                           f"{MAX_RESPONSE_BYTES}-byte limit for an OAuth JSON "
                                           f"response")
                return json.loads(body)
    except DiscordAuthError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as ex:
        # the failure CLASS is safe to log; the URL and the body are not included on purpose
        raise DiscordAuthError(f"the request to Discord failed ({type(ex).__name__})") from None


# --------------------------------------------------------------------------- the configuration

@dataclass(frozen=True, slots=True)
class DiscordSettings:
    """The resolved ``auth.discord`` block. Carries the secret: never log or render this object."""
    client_id: str
    client_secret: str
    public_base_url: str
    member_ttl: int = DEFAULT_MEMBER_TTL


def _validated_member_ttl(value) -> int:
    """The per-request resolution TTL, clamped to :data:`MIN_MEMBER_TTL`..:data:`MAX_MEMBER_TTL`.

    Clamped rather than refused, with a warning: this is a performance/staleness knob, not a
    security boundary, and a typo in it must not stop the console from starting. ``0`` (and any
    non-integer) would silently disable the per-request cache, which is why neither is accepted
    as-is.
    """
    if value is None:
        return DEFAULT_MEMBER_TTL
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            log.warning("auth.discord.member_ttl must be a whole number of seconds, got %r - using "
                        "the default of %ds.", value, DEFAULT_MEMBER_TTL)
            return DEFAULT_MEMBER_TTL
    if value < MIN_MEMBER_TTL:
        log.warning("auth.discord.member_ttl %ds is below the minimum - using %ds.",
                    value, MIN_MEMBER_TTL)
        return MIN_MEMBER_TTL
    if value > MAX_MEMBER_TTL:
        log.warning("auth.discord.member_ttl %ds is above the maximum - using %ds.",
                    value, MAX_MEMBER_TTL)
        return MAX_MEMBER_TTL
    return value


def _secret_from_store(key: str, config_dir: str | Path) -> str:
    """Read a secret from ``config/.secret/<key>.pkl`` through ``utils.get_password``.

    Imported INSIDE the function on purpose: the store is read only when a secret is actually
    needed, and ``core.utils.os`` drags in the bot's ``core`` package — a heavy dependency for a
    path most installs never take. This is NOT a promise that the module is core-free: it reaches
    ``core`` at import time through ``..scope`` (see the module docstring).
    """
    from core.utils.os import get_password

    try:
        return str(get_password(key, config_dir=str(config_dir)))
    except ValueError:
        raise ValueError(f"auth.discord.client_secret_key names '{key}', but there is no "
                         f"{SECRET_STORE_DIR}/{key}.pkl in the config directory. Store it with "
                         f"`python -c \"from core.utils.os import set_password; "
                         f"set_password('{key}', '<the secret>')\"`.")


def _validated_base_url(public_base_url) -> str:
    """The configured public origin, or a loud refusal. NEVER derived from a request."""
    base = str(public_base_url or "").strip()
    if not base:
        raise ValueError("auth.discord.enabled is true but auth.public_base_url is not set. The "
                         "discord redirect URI is built from it (never from a request header), so "
                         "there is nothing to redirect Discord back to.")
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"auth.public_base_url must be an absolute http(s) URL, got '{base}'")
    return base.rstrip("/")


def parse_discord_config(discord_config, *, config_dir: str | Path,
                         public_base_url=None,
                         secret_reader: DiscordSecretReader | None = None) -> DiscordSettings | None:
    """Resolve ``auth.discord``, or ``None`` when it is absent or not enabled.

    Raises ``ValueError`` when it IS enabled and unusable (no client id, no secret, a missing or
    malformed ``public_base_url``): a half-configured backend is discovered at startup, not in the
    middle of somebody's login. ``secret_reader`` is the seam a test uses to stand in for the
    pickle store without importing the bot's ``core``.
    """
    if not isinstance(discord_config, dict):
        log.info("Admin web UI auth: auth.discord is not configured - no Discord login.")
        return None
    if discord_config.get("enabled", False) is not True:
        log.info("Admin web UI auth: the Discord backend is disabled (auth.discord.enabled is "
                 "not true).")
        return None

    client_id = str(discord_config.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("auth.discord.enabled is true but auth.discord.client_id is missing "
                         "(the Discord application's client id).")

    # PRECEDENCE (pinned by the lead): the NAMED STORE KEY wins when it is set; the inline value
    # is the fallback. A named key that yields nothing is a refusal, not a silent fallback to the
    # inline value: the operator asked for a specific secret and did not get it.
    inline = discord_config.get("client_secret")
    key = str(discord_config.get("client_secret_key") or "").strip()
    secret = ""
    if key:
        reader = secret_reader or _secret_from_store
        secret = str(reader(key, config_dir) or "").strip()
        if not secret:
            raise ValueError(f"auth.discord.client_secret_key names '{key}', but the secret store "
                             f"holds no value for it ({SECRET_STORE_DIR}/{key}.pkl).")
    elif isinstance(inline, str) and inline.strip():
        secret = inline.strip()
    if not secret:
        raise ValueError("auth.discord.enabled is true but no client secret is configured: set "
                         "auth.discord.client_secret (inline) or auth.discord.client_secret_key "
                         f"(a key in {SECRET_STORE_DIR}/<key>.pkl).")

    return DiscordSettings(client_id=client_id, client_secret=secret,
                           public_base_url=_validated_base_url(public_base_url),
                           member_ttl=_validated_member_ttl(discord_config.get("member_ttl")))


# --------------------------------------------------------------------------- the backend

class DiscordOAuthBackend(AuthBackend):
    """Discord OAuth (``identify`` + ``guilds.members.read``), member resolved through the bot."""

    name = BACKEND_NAME
    supports_password = False

    def __init__(self, settings: DiscordSettings):
        self._settings = settings
        #: subject -> (member snapshot, fetched_at). Only used when the bot has NO member list.
        self._resolution: dict[str, tuple[object, float]] = {}
        #: subject -> the earliest time a background refresh may be attempted again (one per TTL,
        #: and the growing backoff while the bot cannot answer)
        self._retry_at: dict[str, float] = {}
        #: subject -> the current backoff step (seconds), doubled on each unanswered retry
        self._backoff: dict[str, float] = {}
        #: subject -> WHEN the member resolution stopped being able to ANSWER. While this window is
        #: open the session and its remembered resolution are KEPT (a bot restart is not a
        #: membership fact), no data is served, and the refusal is RETRYABLE — see :meth:`authenticate`
        #: and ``permissions.mark_identity_unverifiable``.
        self._unverifiable: dict[str, float] = {}
        #: the in-flight background refreshes (awaited by the tests, discarded on completion)
        self._refreshes: set[asyncio.Task] = set()
        #: the per-request refusals that are CONDITIONS, not incidents: the first occurrence is
        #: explained in full, the rest are counted (see permissions.ConditionLog). Held per backend,
        #: so a fresh app (a takeover) starts with a clean log — the state is about THIS backend.
        self._refusals = permissions.ConditionLog(log)

    # the client id is public (it travels in the authorize URL); the secret is not exposed at all
    @property
    def client_id(self) -> str:
        return self._settings.client_id

    @property
    def member_ttl(self) -> int:
        return self._settings.member_ttl

    def redirect_uri(self) -> str:
        """The redirect URI Discord must call back: config only, never a request header."""
        return f"{self._settings.public_base_url}{CALLBACK_PATH}"

    def authorize_redirect(self, state: str) -> str:
        """The authorize URL the start route redirects to."""
        query = urlencode({"client_id": self._settings.client_id, "response_type": "code",
                           "scope": SCOPE, "redirect_uri": self.redirect_uri(), "state": state})
        return f"{AUTHORIZE_URL}?{query}"

    # ----------------------------------------------------------------- per request

    def authenticate(self, request: Request) -> Identity | None:
        """The identity this session holds, re-resolved against the LIVE bot on every request.

        The cookie carries ``{"backend": "discord", "subject": "<id>"}`` and nothing else, so a
        member removed from the guild loses every capability on their next request, on the same
        cookie.

        Two resolution paths, decided by the bot's intents:

        * the bot CAN see members (the Server Members Intent is on): ``guild.get_member`` against the
          live cache, exactly like the rest of the bot — a role change is effective immediately and
          no API call happens;
        * the bot CANNOT (``privileged_intents: false``): the live cache is empty for everyone, so
          the answer comes from the short-TTL resolution cache the LOGIN seeded
          (``auth.discord.member_ttl``). This method is sync and stays cheap: it never calls
          Discord. An entry older than the TTL is still served (the request must not fail just
          because the entry aged out) while ONE background refresh runs — so the value is at most
          one TTL old plus one refresh.

        THREE OUTCOMES, and the third one is the fix this card is about. When the member cannot be
        resolved the question is WHY:

        * the bot's member list is authoritative and the person is not in it — a DEFINITIVE
          negative: refuse, as this method always did;
        * the bot cannot ANSWER (no member list, its lookup raised, the guilds have not loaded yet,
          a rate limit, a timeout) — INDECISIVE: the session and the remembered resolution are KEPT,
          the lookup is retried in the background with backoff, and the request is answered with a
          RETRYABLE refusal (:data:`MEMBER_RETRY_AFTER` seconds, ``permissions.RETRYABLE_STATUS``
          status), never with 401/403. The window is
          remembered per subject in :attr:`_unverifiable`, and the fact is recorded on the request
          (``permissions.mark_identity_unverifiable``) so the access gate can turn it into a 503
          with a ``Retry-After``. No data is served in either case: fail closed is unchanged.
        """
        ref = session_identity_ref(request)
        if ref is None or ref.get("backend") != self.name:
            return None
        subject = str(ref.get("subject") or "").strip()
        if not subject:
            # an invalid session (the reference names the backend but nobody): expected, one DEBUG
            # line, never a per-request WARNING.
            log.debug("auth: refusing %s %s - the session names the discord backend with no "
                      "subject", request.method, request.url.path)
            return None
        bot = current_bot()
        if bot is None:
            # a CONDITION (early start, a takeover, a bot restart), not N incidents: explained once,
            # then counted. A dashboard polls throughout, so a per-request WARNING here is the flood.
            self._refusals.hit(
                ("bot-unavailable",),
                "auth: refusing %s %s - the bot object is None, so Discord user %s's roles cannot "
                "be resolved", request.method, request.url.path, subject,
                summary=_SUPPRESSED_MEMBER_REFUSALS)
            return None
        member = resolve_member(bot, subject)
        if member is None and not members_intent_available(bot):
            # no member list: fall back to the TTL cache (and refresh it in the background)
            member = self._cached_member(subject, bot)
        if member is None:
            if members_intent_available(bot):
                # DEFINITIVE: the member list is authoritative, and this person is not in it
                self._refusals.hit(
                    ("member-absent",),
                    "auth: refusing %s %s - %s", request.method, request.url.path,
                    member_refusal_reason(bot, subject), summary=_SUPPRESSED_MEMBER_REFUSALS)
                return None
            # INDECISIVE: the bot has no member list and its lookup did not answer. An unanswered
            # question is not a membership fact, so the session is kept and this request is
            # RETRYABLE. The window and the terse one-line report live in _note_unverifiable.
            self._note_unverifiable(subject)
            permissions.mark_identity_unverifiable(request, retry_after=MEMBER_RETRY_AFTER)
            return None
        # resolved: keep the remembered resolution fresh, and close the window if it was open
        self._remember(subject, member)
        return self._identity(subject, member, getattr(bot, "roles", None) or {})

    # ----------------------------------------------------------------- the transient window

    def _note_unverifiable(self, subject: str, *, retry_delay: float | None = None) -> None:
        """Open (or keep open) *subject*'s transient window, and report it ONCE.

        A CONDITION, not an incident: the request path polls throughout, so the explanation is one
        line — the first occurrence, through the SHARED once-per-condition helper in
        ``permissions`` (never a second mechanism) — and the repeats are counted silently, with no
        subject id in the repeating line. The long explanatory paragraph belongs to the one-shot
        notices (the login refusal and the operator-facing reading of them), not to the request path.

        ``retry_delay`` is passed by the BACKGROUND REFRESH when an attempt came back unanswered: it
        is what paces the next attempt (the growing backoff). The request path passes nothing — it
        only reports the condition, and the retry it may schedule is paced by the callers'
        :meth:`_request_refresh`, so a burst of polled requests cannot push the next attempt away.
        """
        self._unverifiable.setdefault(subject, _now())
        if retry_delay is not None:
            self._retry_at[subject] = _now() + retry_delay
        self._refusals.hit(
            ("member-unverifiable",),
            "auth: the Discord member resolution cannot verify a session right now - the refusal "
            "is retryable (HTTP %d, Retry-After %d), the session is kept and the lookup is retried "
            "in the background; the full reason is in the login/start-up notices",
            permissions.RETRYABLE_STATUS, MEMBER_RETRY_AFTER,
            summary=_SUPPRESSED_MEMBER_REFUSALS)

    def _recovered(self, subject: str) -> None:
        """Close *subject*'s transient window, surfacing the recovery ONCE."""
        started = self._unverifiable.pop(subject, None)
        if started is None:
            return
        minutes = max(0, int((_now() - started) / 60.0))
        self._refusals.cleared(
            ("member-unverifiable",),
            "auth: member resolution recovered - %d similar refusal(s) were suppressed while it "
            "was failing (about %d minute(s) of it)", minutes, level=logging.INFO)

    def _next_backoff(self, subject: str) -> float:
        """The next retry delay for *subject*, doubled every time the bot cannot answer."""
        previous = self._backoff.get(subject)
        delay = MEMBER_RETRY_MIN if previous is None else min(previous * 2.0, MEMBER_RETRY_MAX)
        self._backoff[subject] = delay
        return delay

    # ----------------------------------------------------------------- the resolution cache

    def _remember(self, subject: str, member) -> None:
        """Store a resolution for *subject* (a snapshot, see :func:`member_roles_snapshot`).

        A resolved answer is the one thing that closes the transient window, so the recovery is
        surfaced here — wherever the answer came from (the login, a background refresh, or the live
        cache answering a request).
        """
        self._resolution[subject] = (member_roles_snapshot(member), _now())
        self._retry_at.pop(subject, None)
        self._backoff.pop(subject, None)
        self._recovered(subject)

    def _cached_member(self, subject: str, bot=None):
        """The remembered member, refreshed in the background; ``None`` when it may not be served.

        * fresh entry (age <= TTL), no window open: served as-is, no Discord call;
        * stale entry, no window open: served ONCE while a background refresh runs, so the value is
          at most one TTL old plus the duration of one refresh — and a request never fails just
          because the entry aged out;
        * no entry, or an OPEN window (the last re-check could not answer): ``None`` (fail closed)
          while the refresh runs, so the next request is served.

        The refresh is attempted at most once per retry window per subject (``_retry_at`` — the TTL
        normally, the growing backoff while the bot cannot answer), so a broken Discord connection
        cannot turn into a request-per-refresh storm.
        """
        now = _now()
        entry = self._resolution.get(subject)
        if entry is not None and now - entry[1] <= self._settings.member_ttl \
                and subject not in self._unverifiable:
            return entry[0]
        self._request_refresh(subject, bot)
        if entry is None or subject in self._unverifiable:
            return None
        return entry[0]

    def _request_refresh(self, subject: str, bot=None) -> None:
        """Schedule ONE background refresh of *subject*, if the retry window has passed."""
        now = _now()
        if self._retry_at.get(subject, 0.0) > now:
            return
        self._retry_at[subject] = now + self._settings.member_ttl
        self._schedule_refresh(subject, bot)

    def _schedule_refresh(self, subject: str, bot=None) -> None:
        """Queue one background REST refresh of *subject* (best effort, never blocking a request)."""
        if bot is None:
            bot = current_bot()
        if bot is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # no event loop on this thread: the next request on a loop will schedule it again
            log.debug("auth: no running event loop - the member refresh for Discord user %s is "
                      "deferred", subject)
            return
        task = loop.create_task(self._refresh(subject, bot))
        self._refreshes.add(task)
        task.add_done_callback(self._refreshes.discard)

    async def _refresh(self, subject: str, bot) -> None:
        """The background refresh, through the SAME chain as the login.

        It calls :func:`resolve_login_member` — the live member cache, then the bot-side
        single-member lookup, then the user-side ``members.read`` lookup when a token is available
        (the login's token is never persisted, so in the background the first two are what run) —
        instead of a second, bot-side-only lookup of its own.

        THE THREE OUTCOMES decide what happens to the remembered resolution:

        * RESOLVED: remembered, and the transient window (if any) is closed with one line;
        * DEFINITIVE negative (the bot's authoritative member list answered "nobody"): the remembered
          resolution is dropped, exactly as before — the session is refused from now on;
        * INDECISIVE (no member list, a REST error, the bot not ready, a rate limit, a timeout): the
          resolution is KEPT, the transient window opens/continues, and the next attempt is paced by
          a growing backoff. An unanswered question is never turned into a membership fact.
        """
        try:
            lookup = await resolve_login_member(bot, subject, None)
        except Exception as ex:                       # pragma: no cover - belt and braces
            log.warning("auth: the background member refresh for Discord user %s raised (%s)",
                        subject, type(ex).__name__)
            lookup = MemberLookup(None, "unknown")
        if lookup.member is not None:
            self._remember(subject, lookup.member)
            return
        if lookup.definitive:
            self._resolution.pop(subject, None)
            self._refusals.hit(
                ("member-absent",),
                "auth: the member resolution answered - Discord user %s is not a member of the "
                "bot's guild, so the session is refused from now on", subject,
                summary=_SUPPRESSED_MEMBER_REFUSALS)
            return
        self._note_unverifiable(subject, retry_delay=self._next_backoff(subject))

    # ----------------------------------------------------------------- the login itself

    async def complete_login(self, code: str, redirect_uri: str) -> Identity:
        """Exchange the code, fetch the user, resolve the member and return the identity.

        The member is resolved by :func:`resolve_login_member` (live cache, then the bot's own REST
        lookup, then the user's) and the path that answered is logged ONCE, here — that line is how
        an operator learns which path their ``privileged_intents`` setting exercises. The resolution
        also SEEDS the per-request TTL cache, so the first page load after the redirect does not
        have to wait for a refresh.

        Raises :class:`DiscordAuthError` with a loggable reason; the route renders one generic
        sentence and never the reason.
        """
        if not code:
            raise DiscordAuthError("the callback carried no authorization code")
        payload = await exchange_code(self._settings.client_id, self._settings.client_secret,
                                      code, redirect_uri)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise DiscordAuthError("the token exchange returned no access_token")
        user = await fetch_user(token)
        discord_id = str(user.get("id") or "").strip()
        if not discord_id:
            raise DiscordAuthError("the user lookup returned no id")
        bot = current_bot()
        if bot is None:
            raise DiscordAuthError("the bot object is None (early start, after a takeover, or a "
                                   "headless install), so the member's roles cannot be resolved")
        lookup = await resolve_login_member(bot, discord_id, token)
        if lookup.member is None:
            raise DiscordAuthError(member_refusal_reason(bot, discord_id))
        self._remember(discord_id, lookup.member)
        log.info("auth: resolved Discord user %s via %s", discord_id,
                 LOOKUP_LABELS.get(lookup.source, lookup.source))
        return self._identity(discord_id, lookup.member, getattr(bot, "roles", None) or {})

    def _identity(self, discord_id: str, member, role_map) -> Identity:
        """The identity for a resolved member: roles PLUS the name and avatar the shell shows.

        The presentation is taken from the SAME member the roles came from (no extra lookup), and
        the avatar URL is passed through :func:`safe_avatar_url` here — the page applies the same
        rule again before rendering it into the ``img`` tag.

        The SCOPE comes from the same walk, and that is the whole point of doing it here: the
        ``managed_by`` comparison is against the member's Discord role ids and names (the pair
        ``check_roles`` uses), so no Discord call is added — and a scoped caller's servers are
        decided from the LIVE member, per request, exactly like the roles. An ``Admin`` is unscoped
        by the rule in :meth:`AuthManager.scope_for`, which is the one place that can grant it.
        """
        roles = role_names_for_member(member, role_map)
        display_name, avatar_url = member_presentation(member)
        return Identity(subject=str(discord_id), backend=self.name, roles=frozenset(roles),
                        display_name=display_name, avatar_url=safe_avatar_url(avatar_url),
                        scope=Scope.restricted(member_scope_tokens(member)))


def discord_backend(discord_config, *, config_dir: str | Path, public_base_url=None,
                    secret_reader: DiscordSecretReader | None = None
                    ) -> DiscordOAuthBackend | None:
    """The backend for ``auth.discord``, or ``None`` when it is absent or not enabled."""
    settings = parse_discord_config(discord_config, config_dir=config_dir,
                                    public_base_url=public_base_url, secret_reader=secret_reader)
    if settings is None:
        return None
    log.info("Admin web UI auth: the Discord backend is enabled (client id %s, redirect URI %s, "
             "client secret from %s).", settings.client_id, f"{settings.public_base_url}"
             f"{CALLBACK_PATH}",
             "auth.discord.client_secret" if not _uses_store(discord_config) else "the secret store")
    return DiscordOAuthBackend(settings)


def _uses_store(discord_config: dict) -> bool:
    """Whether the resolved secret came from the store (for the startup log line only)."""
    return bool(str(discord_config.get("client_secret_key") or "").strip())


# --------------------------------------------------------------------------- the routes

def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers (see the registrar)."""
    from .. import permissions
    return {START_PATH: permissions.PUBLIC, CALLBACK_PATH: permissions.PUBLIC}


def add_routes(router: APIRouter) -> APIRouter:
    """Add the OAuth start and callback routes to the shell's router and return it.

    Both are declared PUBLIC (they are the door, and the gate is deny-by-default). The routes are
    registered whether or not the backend is enabled: a disabled backend REFUSES with a page, so a
    deployment that turns it on later does not have to be restarted for the paths to exist.
    """

    @router.get(START_PATH, response_class=HTMLResponse)
    async def discord_start(request: Request, next: str = SITE_ROOT):
        backend = _backend(request)
        if backend is None:
            log.info("auth: refusing %s %s - the Discord backend is not enabled",
                     request.method, request.url.path)
            return error_page(request, DISABLED_MESSAGE, status_code=404)
        # the landing target is a query parameter, so it is attacker-controlled: validate it with
        # the SAME rule the login form uses, and carry the RESULT (not the parameter) in the session
        from .routes import _safe_next
        landing = _safe_next(next)
        state = begin_flow(request, landing)
        log.info("auth: starting the Discord OAuth flow (%s %s)", request.method, request.url.path)
        return RedirectResponse(backend.authorize_redirect(state), status_code=302)

    @router.get(CALLBACK_PATH, response_class=HTMLResponse)
    async def discord_callback(request: Request):
        backend = _backend(request)
        if backend is None:
            log.info("auth: refusing %s %s - the Discord backend is not enabled",
                     request.method, request.url.path)
            return error_page(request, DISABLED_MESSAGE, status_code=404)

        params = request.query_params
        if params.get("error"):
            # a cancel: judge the state READ-ONLY and only then drop the pending flow — a request
            # anybody can type must not be able to clear somebody else's session state
            if match_flow(request, params.get("state")):
                discard_flow(request)
            log.info("auth: the Discord authorization was cancelled (%s %s)", request.method,
                     request.url.path)
            return error_page(request, CANCELLED_MESSAGE, status_code=400)

        landing = finish_flow(request, params.get("state"))
        if landing is None:
            return error_page(request, GENERIC_MESSAGE, status_code=400)
        code = params.get("code")
        try:
            identity = await backend.complete_login(str(code or ""), backend.redirect_uri())
        except DiscordAuthError as ex:
            log.warning("auth: refusing a Discord login - %s", ex)
            return error_page(request, GENERIC_MESSAGE, status_code=400)
        except Exception:
            # an unexpected failure is a log line with a traceback (no request data) and the same
            # generic page -- never a 500 body and never a detail
            log.exception("auth: the Discord login failed unexpectedly")
            return error_page(request, GENERIC_MESSAGE, status_code=400)

        # session fixation: nothing about the pre-login session survives into the authenticated
        # one. The landing path was read above, before the clear.
        clear_session_identity(request)
        request.session.clear()
        set_session_identity(request, identity)
        log.info("auth: Discord user %s signed in via the %s backend (%d role(s))",
                 identity.subject, identity.backend, len(identity.roles))
        return RedirectResponse(landing, status_code=303)

    return router


def _backend(request: Request) -> DiscordOAuthBackend | None:
    """The enabled Discord backend of this application, or ``None``."""
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return None
    backend = manager.backend(BACKEND_NAME)
    return backend if isinstance(backend, DiscordOAuthBackend) else None


def error_page(request: Request, message: str, *, status_code: int = 400) -> HTMLResponse:
    """The page a refused, failed or CANCELLED Discord sign-in shows.

    IT IS THE CONSOLE'S REAL LOGIN PAGE — rendered by the same renderer and template the login
    route uses (``auth.routes.render_login_page``), so the doors, the status pill, the styling and
    the copy are the config-driven ones. The previous hand-built document advertised the
    username/password door unconditionally, which an installation can have turned OFF (Frank hit
    exactly that), printed the status code at the person, and looked nothing like the console.

    * ``message`` becomes the page's error line. It is one of this module's OWN generic sentences —
      no ``error_description`` and no other query text is ever reflected into the page (Discord's
      words are not echoed, and a query anybody can type must not write into the page);
    * no status code is printed on the page (the response STATUS is unchanged — a wire contract);
    * a door this installation has turned off is ABSENT from the page, exactly as on the login page.

    (The import is inside the function: ``auth.routes`` imports this module.)
    """
    from .routes import render_login_page
    return render_login_page(request, error=message, status_code=status_code)
