"""Steps 6 and 7 of the tour — Discord: the bot, and its roles and channels.

THE ANSWERS ARE THE INSTALLER'S INPUTS. :class:`BotAnswers` carries exactly what ``install.py``
collects in its Discord Setup block — the guild id, the owner, the member-count decision (kept as
the ``privileged_intents`` value it decides) — and :class:`RoleAnswers` carries the three-row role
mapping, the optional audit channel and the admin-channel choice. :func:`main_block` /
:func:`bot_block` turn them into the shapes the installer writes through the shared
:func:`core.utils.setup_config.build_main` / :func:`build_discord_bot` builders. Nothing here writes
a file: the tour's draft is the destination, and the Review step builds the configuration.

THE TOKEN IS NEVER WRITTEN DOWN. ``install.py`` keeps the bot token in ``config/.secret/token.pkl``
(``utils.set_password``) and never in ``bot.yaml``; the tour keeps it in PROCESS MEMORY ONLY
(:func:`token_store`) — not in the draft file, which sits in plain text in a config directory that
does not exist yet. The Review step turns the held token into ``config/.secret/token.pkl``; a
console restart therefore asks for it again, exactly as the database step's passwords do.

THE MEMBER-COUNT SWITCH DECIDES ``privileged_intents``. Answered "more than 10000 members" the bot
cannot hold the Server Members Intent, so ``privileged_intents`` is ``false`` and the bot needs the
"View Audit Log" Discord permission to follow joins and leaves — the installer's own consequence
sentence. The switch is stored as ``privileged_intents`` (its default, ``true``, is the installer's).
"""

from __future__ import annotations

import asyncio
import logging
import re

from dataclasses import dataclass, field
from typing import Any, Mapping

from core.utils import setup_config

__all__ = [
    "TOKEN_KEY",
    "CLIENT_SECRET_KEY",
    "FETCH_TIMEOUT",
    "DEFAULT_ROLE_NAMES",
    "DEFAULT_ROLE_DEFAULTS",
    "ChannelOption",
    "GuildData",
    "BotAnswers",
    "RoleAnswers",
    "token_store",
    "client_secret_store",
    "application_id",
    "invite_url",
    "invite_permissions",
    "BOT_INVITE_SCOPE",
    "NoGuild",
    "MultipleGuilds",
    "guild_cache",
    "guild_status",
    "cached_guild",
    "fetch_guild_data",
    "fetch_guilds",
    "leave_others",
    "parse_id",
    "parse_channel",
    "answers_from_mapping",
    "roles_from_mapping",
    "non_secret_document",
    "roles_document",
    "resolve_bot",
    "resolve_roles",
    "main_block",
    "roles_block",
    "bot_block",
]

log = logging.getLogger(__name__)

#: the process store's key for the typed bot token (see the module docstring)
TOKEN_KEY = "token"

#: the secret-store key the console's Discord login reads its client secret from
#: (``config/.secret/discord_client_secret.pkl``; the schema example names it too).
CLIENT_SECRET_KEY = "discord_client_secret"

#: How long the whole Discord read may take. The read is five REST calls; a hung DNS must fail the
#: step, never hang the request handler.
FETCH_TIMEOUT = 10.0

#: The OAuth2 scope a bot invite carries (``scope=bot``); the ``applications.commands`` scope is added
#: by Discord for a bot that publishes slash commands and is not part of this project's invite.
BOT_INVITE_SCOPE = "bot"


class NoGuild(Exception):
    """The token is valid but the bot is a member of NO guild (the invite guide's own state)."""


class MultipleGuilds(Exception):
    """The token is valid but the bot is a member of more than one guild.

    Carries the bot's own guild list (``(id, name)``) so the step can name where it is invited and
    offer to leave the ones the operator does not keep.
    """

    def __init__(self, guilds):
        super().__init__(f"the bot is a member of {len(guilds)} guilds")
        self.guilds = tuple((int(gid), str(name)) for gid, name in guilds)


# --------------------------------------------------------------------------- the Discord connection

@dataclass(frozen=True)
class ChannelOption:
    """One selectable guild channel: its id, its name and its category (the ``<optgroup>`` label)."""

    id: int
    name: str
    category: str = ""


@dataclass(frozen=True)
class GuildData:
    """What one guild read returned: its name, its channels, its roles, the bot's own guild list and
    whether the bot holds Administrator in that guild.

    A LIVE MACHINE FACT, held in memory only (see :func:`guild_cache`): a guild's layout goes stale the
    moment a channel is renamed, so it is never written to the tour's draft. The roles exclude
    ``@everyone`` and every bot-managed/integration role — offering one would silently make a role
    mean \"everyone\" or name a role no human can hold.

    ``bot_is_admin`` is ``True`` or ``False`` when the bot's own membership was read, and ``None``
    when it could NOT be determined — an unknown permission state is never reported as Administrator.
    """

    guild_id: int
    guild_name: str
    channels: tuple[ChannelOption, ...] = ()
    roles: tuple[str, ...] = ()
    guilds: tuple[tuple[int, str], ...] = ()
    bot_is_admin: bool | None = None


def client_secret_store(app) -> dict:
    """The per-application dict holding the console's Discord client secret for this process.

    The same process-lifetime rule as the bot token (:func:`token_store`): never the draft file (plain
    text on disk), and only turned into ``config/.secret/discord_client_secret.pkl`` by the Review
    step's commit.
    """
    store = getattr(app.state, "setup_discord_client_secret", None)
    if not isinstance(store, dict):
        store = {}
        app.state.setup_discord_client_secret = store
    return store


def application_id(token: str | None) -> int | None:
    """The Discord APPLICATION id a bot token belongs to, or ``None``.

    A bot token's first dot-separated segment is the base64 of the application's snowflake id, so the
    OAuth ``client_id`` is DERIVED rather than asked for — the operator already pasted the token, and
    asking twice is one decision more than a running install needs.
    """
    import base64

    segment = str(token or "").split(".")[0]
    if not segment:
        return None
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        value = int(decoded.decode("ascii"))
    except Exception:  # noqa: BLE001 - a malformed token segment yields None
        return None
    return value if value > 0 else None


def invite_permissions() -> int:
    """The invite link's permissions integer, derived from the project's OWN named permission set.

    The names are ``core.const.DEFAULT_CHANNEL_PERMISSIONS`` — the exact set ``install.py`` prints as
    the channel permissions DCSServerBot needs (View Channel, Send Messages, Read Messages, Read
    Message History, Add Reactions, Attach Files, Embed Links, Manage Messages). Discord derives the
    bitfield from those names; the integer is never written down by hand.
    """
    import discord

    from core.const import DEFAULT_CHANNEL_PERMISSIONS

    return discord.Permissions(**{name: True for name in DEFAULT_CHANNEL_PERMISSIONS}).value


def invite_url(token: str | None) -> str | None:
    """The OAuth2 authorize URL that invites THIS bot, or ``None`` without a derivable application id.

    The application id is what a successful token read DERIVES (:func:`application_id`), so no second
    question is asked: the operator already pasted the token.
    """
    app_id = application_id(token)
    if app_id is None:
        return None
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&scope={BOT_INVITE_SCOPE}&permissions={invite_permissions()}")


def guild_cache(app) -> dict:
    """The per-application guild cache, keyed on ``(token, guild_id)`` (see :func:`fetch_guild_data`)."""
    cache = getattr(app.state, "setup_guild_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        app.state.setup_guild_cache = cache
    return cache


def guild_status(app) -> dict:
    """The last read's status: ``{"key": (token, guild_id), "code": <failure code or "">}``.

    Read by the steps that render pickers so a failed read is SAID rather than silently degrading:
    the state is derived from this in-memory status and the cache, never from the draft.
    """
    status = getattr(app.state, "setup_guild_status", None)
    if not isinstance(status, dict):
        status = {}
        app.state.setup_guild_status = status
    return status


def cached_guild(app, token, guild_id) -> GuildData | None:
    """The guild read for exactly this ``(token, guild_id)``, or ``None``.

    Keyed on BOTH values on purpose: changing the token or the guild id must never serve the other
    one's channels — the cache cannot go stale for a key it does not hold.
    """
    if not token or guild_id is None:
        return None
    cached = guild_cache(app).get((str(token), int(guild_id)))
    return cached if isinstance(cached, GuildData) else None


def fetch_status(app, token, guild_id) -> str | None:
    """The failure code of the last read FOR THIS ``(token, guild_id)``, or ``None``.

    ``None`` means no read has been attempted for this key yet; ``""`` means the last read succeeded.
    """
    status = guild_status(app)
    if not token or guild_id is None:
        return None
    if status.get("key") != (str(token), int(guild_id)):
        return None
    return str(status.get("code", ""))


def fetch_guilds(app, token, guild_id) -> tuple[tuple[int, str], ...]:
    """The bot's OWN guild list from the last read for exactly this ``(token, guild_id)``, or ``()``.

    Held in the read's own status (never the draft): it is a live machine fact, and it is what the
    no-guild and too-many-guilds guides name. A key the last read did not cover yields ``()``.
    """
    status = guild_status(app)
    if not token or guild_id is None:
        return ()
    if status.get("key") != (str(token), int(guild_id)):
        return ()
    guilds = status.get("guilds") or ()
    return tuple((int(gid), str(name)) for gid, name in guilds)


async def fetch_guild_data(app, token, guild_id) -> tuple[GuildData | None, str]:
    """Read *guild_id* with *token*, cache it by ``(token, guild_id)``, and classify any failure.

    The read reuses the in-memory cache for the SAME key, so a re-render never repeats the network
    call and a changed token or guild id re-fetches. The failure code is one of ``""`` (success),
    ``discord_bad_token``, ``discord_no_guild``, ``discord_multiple_guilds``, ``discord_guild_unknown``,
    ``discord_forbidden``, ``discord_rate_limited`` or ``discord_unreachable``. Never raises: the step
    continues with typed values, so a broken Discord must not break a render.

    The bot's own guild list travels with the result — in the status, NOT the cache: a failed read
    (the two guild-count states and every error) is not cached, but its list is what the guide names.
    """
    if not token or guild_id is None:
        return None, "discord_unreachable"
    key = (str(token), int(guild_id))
    cached = guild_cache(app).get(key)
    if isinstance(cached, GuildData):
        guild_status(app).update({"key": key, "code": "", "guilds": cached.guilds})
        return cached, ""
    data, code = await _read_guild(str(token), int(guild_id))
    if code == "" and data is not None:
        guild_cache(app)[key] = data
    guilds = data.guilds if data is not None else ()
    guild_status(app).update({"key": key, "code": code, "guilds": guilds})
    return data, code


async def _read_guild(token: str, guild_id: int) -> tuple[GuildData | None, str]:
    """Perform the HTTP-only guild read and classify its outcome (never raises)."""
    import discord

    try:
        return await asyncio.wait_for(_read(token, guild_id), timeout=FETCH_TIMEOUT), ""
    except asyncio.TimeoutError:
        log.warning("Setup tour: the Discord read timed out.")
        return None, "discord_unreachable"
    except discord.LoginFailure:
        log.warning("Setup tour: Discord refused the bot token.")
        return None, "discord_bad_token"
    except NoGuild:
        log.warning("Setup tour: the bot is not a member of any guild.")
        return None, "discord_no_guild"
    except MultipleGuilds as ex:
        log.warning("Setup tour: the bot is a member of more than one guild.")
        # no data to cache: the read FAILED (the step's gate stays closed), but the guild list rides
        # along so the guide can name every server and the chooser can offer to leave the others.
        return GuildData(guild_id=int(guild_id), guild_name="", guilds=ex.guilds), \
            "discord_multiple_guilds"
    except discord.NotFound:
        log.warning("Setup tour: the bot is not in the guild (or the id is wrong).")
        return None, "discord_guild_unknown"
    except discord.Forbidden:
        log.warning("Setup tour: the bot cannot read that guild.")
        return None, "discord_forbidden"
    except discord.HTTPException as ex:
        # a rate limit discord.py did not retry away, or any other API-side refusal. The cause is
        # logged; the token and the Authorization header never are.
        log.warning("Setup tour: Discord answered with an error during the read (%s).",
                    type(ex).__name__)
        return None, "discord_rate_limited"
    except Exception as ex:  # noqa: BLE001 - any network failure is the same honest answer
        log.warning("Setup tour: the Discord read failed (%s); continuing with typed values.",
                    type(ex).__name__)
        return None, "discord_unreachable"


async def _read(token: str, guild_id: int) -> GuildData:
    """Log in with the token over REST only, read the bot's guilds and the requested guild, then close.

    ``Intents.none()`` and no ``connect()``: this is an HTTP client, never a gateway session. ``login``
    IS the validation (it is the ``GET /users/@me`` the token is checked by). The bot's OWN guild list
    is read FIRST, so the no-guild and too-many-guilds states are classified as themselves rather than
    as the ``NotFound``/``Forbidden`` the requested-guild read raises for both of them. The bot's own
    membership is then read so the step knows whether it already holds Administrator
    (:func:`_bot_administrator`); a failure there yields ``None``, never a guess. The client is always
    closed in a ``finally`` so neither the aiohttp session nor the token it carries outlives the call.
    """
    import discord

    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        guilds = await _guild_list(client)
        if not guilds:
            raise NoGuild()
        if len(guilds) > 1:
            raise MultipleGuilds(guilds)
        # fetch_guild alone carries NO channels/roles (its own docstring), so both are read explicitly
        guild = await client.fetch_guild(guild_id, with_counts=True)
        channels = await guild.fetch_channels()
        roles = await guild.fetch_roles()
        admin = await _bot_administrator(client, guild)
        return _as_guild_data(guild_id, guild, channels, roles, guilds, admin)
    finally:
        await client.close()


async def _bot_administrator(client, guild) -> bool | None:
    """Whether the bot holds Administrator in *guild*, read over the token's own client.

    The bot's OWN member is read (``GET /guilds/<id>/members/<bot_id>``, no privileged intent) and
    its guild permissions come from discord.py, which applies the guild-owner rule and the
    Administrator implication. ``None`` when the bot's id is unknown or the member read fails — an
    UNDETERMINED permission state is never reported as Administrator.
    """
    bot_id = getattr(getattr(client, "user", None), "id", None) or getattr(client, "application_id",
                                                                            None)
    if not bot_id:
        return None
    try:
        member = await guild.fetch_member(int(bot_id))
    except Exception as ex:  # noqa: BLE001 - an unreadable state is not "admin"
        log.warning("Setup tour: the bot's own membership could not be read (%s).",
                    type(ex).__name__)
        return None
    return bool(member.guild_permissions.administrator)


async def _guild_list(client) -> tuple[tuple[int, str], ...]:
    """The bot's own guilds as ``(id, name)`` pairs (a nameless/idless entry is dropped)."""
    guilds: list[tuple[int, str]] = []
    async for listed in client.fetch_guilds(limit=200):
        if listed.id is not None and listed.name:
            guilds.append((int(listed.id), str(listed.name)))
    return tuple(guilds)


async def leave_others(token, keep_id) -> dict | None:
    """Make the bot leave every guild it is in EXCEPT *keep_id*, over the token's own HTTP call.

    Returns ``None`` when *token* cannot be logged in or *keep_id* is not a guild the bot is in — the
    caller refuses and asks again. Otherwise a PER-GUILD report:
    ``{"kept": (id, name), "left": [(id, name)], "failed": [(id, name, reason)]}``.

    The kept guild is NEVER left (it is skipped by id, before any call). A guild that refuses to be
    left is reported and does not stop the rest, so the operator can always kick it by hand.
    """
    import discord

    if not token or keep_id is None:
        return None
    keep = int(keep_id)
    client = discord.Client(intents=discord.Intents.none())
    try:
        try:
            await client.login(str(token))
            guilds = await asyncio.wait_for(_guild_list(client), timeout=FETCH_TIMEOUT)
        except Exception as ex:  # noqa: BLE001 - a failed login/list is the caller's refusal
            log.warning("Setup tour: the guild list could not be read for the leave (%s).",
                        type(ex).__name__)
            return None
        names = {gid: name for gid, name in guilds}
        if keep not in names:
            return None
        left: list[tuple[int, str]] = []
        failed: list[tuple[int, str, str]] = []
        for gid, name in guilds:
            if gid == keep:
                continue
            try:
                await asyncio.wait_for(client.http.leave_guild(gid), timeout=FETCH_TIMEOUT)
                left.append((gid, name))
            except Exception as ex:  # noqa: BLE001 - per-guild failure is reported, never raised
                log.warning("Setup tour: could not leave guild %s (%s).", gid, type(ex).__name__)
                failed.append((gid, name, type(ex).__name__))
        return {"kept": (keep, names[keep]), "left": left, "failed": failed}
    finally:
        await client.close()


def _as_guild_data(guild_id: int, guild, channels, roles, guilds,
                   bot_is_admin: bool | None = None) -> GuildData:
    """Reduce the discord.py objects to the plain, id/name-only shape the pickers render.

    Category channels are dropped as options (they are the ``<optgroup>`` labels), ``@everyone`` and
    every bot-managed/integration role are dropped, and ordering is Discord's own (position, then
    name). *bot_is_admin* is passed through as read (``None`` when it could not be determined).

    THE CATEGORY NAME IS RESOLVED FROM THE FETCHED LIST, NOT FROM ``channel.category``. On this
    HTTP-only read the guild's channel cache is empty (``fetch_channels`` returns the channels but
    does not add them to ``guild._channels``), so ``channel.category`` — which is
    ``guild.get_channel(category_id)`` — is always ``None``. The parent is therefore looked up by id
    in the category channels of the very list being reduced; the ``channel.category`` lookup is only
    a fallback for a caller that passes a populated guild.
    """
    import discord

    category_names: dict[int, str] = {}
    for channel in channels:
        if getattr(channel, "type", None) != discord.ChannelType.category:
            continue
        if getattr(channel, "id", None) is None or not getattr(channel, "name", None):
            continue
        category_names[int(channel.id)] = str(channel.name)

    options: list[tuple[int, str, str, int]] = []
    for channel in channels:
        if getattr(channel, "type", None) == discord.ChannelType.category:
            continue
        name = getattr(channel, "name", None)
        if getattr(channel, "id", None) is None or not name:
            continue
        parent = getattr(channel, "category_id", None)
        if parent is not None and int(parent) in category_names:
            category = category_names[int(parent)]
        else:
            category = getattr(getattr(channel, "category", None), "name", "") or ""
        options.append((int(getattr(channel, "position", 0) or 0), str(name), str(category),
                        int(channel.id)))
    options.sort(key=lambda row: (row[0], row[1].casefold()))
    channel_options = tuple(ChannelOption(id=row[3], name=row[1], category=row[2]) for row in options)

    role_names: list[str] = []
    for role in roles:
        if getattr(role, "is_default", lambda: False)():
            continue
        if getattr(role, "is_bot_managed", lambda: False)():
            continue
        if getattr(role, "is_integration", lambda: False)():
            continue
        name = getattr(role, "name", None)
        if not name or str(name) in role_names:
            continue
        role_names.append(str(name))
    return GuildData(guild_id=int(guild_id), guild_name=str(getattr(guild, "name", "") or ""),
                     channels=channel_options, roles=tuple(role_names), guilds=tuple(guilds),
                     bot_is_admin=bot_is_admin)

#: the three internal roles and the installer's own defaults (``core.utils.setup_config``)
DEFAULT_ROLE_NAMES = setup_config.DEFAULT_ROLE_NAMES
DEFAULT_ROLE_DEFAULTS = setup_config.DEFAULT_ROLE_DEFAULTS


# ------------------------------------------------------------------------------------ the answers

@dataclass
class BotAnswers:
    """Step 6's answers: the guild id, the owner, and the member-count decision.

    ``privileged_intents`` is the installer's own derivation — ``not "more than 10000 members"`` —
    and its default (``True``) is the installer's default answer.
    """

    guild_id: int | None = None
    owner: int | None = None
    privileged_intents: bool = True


def _default_roles() -> dict[str, tuple[str, ...]]:
    return {name: (default,) for name, default in zip(DEFAULT_ROLE_NAMES, DEFAULT_ROLE_DEFAULTS)}


@dataclass
class RoleAnswers:
    """Step 7's answers: one row per internal role, the audit channel and the admin-channel choice.

    The channel ids are kept AS TYPED: a row that fails validation must come back with the value the
    operator wrote. An empty field means "not set" (the installer's ``-1``).

    ``central_admin`` defaults to ``True``: ONE central admin channel for all servers is the default
    answer, the per-server one the operator has to pick deliberately.
    """

    roles: dict[str, tuple[str, ...]] = field(default_factory=_default_roles)
    audit_channel: str = ""
    admin_channel: str = ""
    #: the "one central admin channel for all servers" choice (the default; per-server is opt-in)
    central_admin: bool = True


def token_store(app) -> dict:
    """The per-application dict holding the typed bot token for this process's lifetime.

    Not the session cookie (``SessionMiddleware`` signs but does not encrypt, so a cookie would put
    the token in the browser) and not the draft file (plain text on disk). Lost on restart, the same
    lifetime the ephemeral session key already has.
    """
    store = getattr(app.state, "setup_discord_token", None)
    if not isinstance(store, dict):
        store = {}
        app.state.setup_discord_token = store
    return store


# ------------------------------------------------------------------------------------ the parsing

def parse_id(value) -> int | None:
    """*value* as a Discord snowflake, or ``None`` when it is not a whole number.

    A negative number is a snowflake the caller invented: refused here rather than written into
    ``main.yaml`` / ``bot.yaml``.
    """
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        return None
    number = int(text)
    return number if number > 0 else None


def parse_channel(value) -> int | None:
    """*value* as a channel id, ``None`` when empty or not a whole number.

    ``-1`` is the installer's "no channel" id and parses.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _as_role(value) -> str | int:
    """One role name or id: digits become an int, exactly as ``install.py`` coerces them."""
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _split_roles(entries) -> tuple[str, ...]:
    """The role values in one row: every entry split on commas, stripped, empties dropped."""
    values: list[str] = []
    for entry in entries:
        for item in str(entry or "").split(","):
            item = item.strip()
            if item and item not in values:
                values.append(item)
    return tuple(values)


def _entries(values: Mapping, key: str) -> list:
    """Every submitted value for *key* (a repeated field), tolerating a plain mapping."""
    getlist: Any = getattr(values, "getlist", None)
    if callable(getlist):
        return list(getlist(key))
    value = values.get(key)
    return [] if value is None else [value]


# ------------------------------------------------------------------------------------ the reading

def answers_from_mapping(data: Mapping | None) -> BotAnswers:
    """Build step 6's answers from the draft's ``discord_bot`` block."""
    data = data if isinstance(data, Mapping) else {}
    return BotAnswers(guild_id=parse_id(data.get("guild_id")),
                      owner=parse_id(data.get("owner")),
                      privileged_intents=bool(data.get("privileged_intents", True)))


def roles_from_mapping(data: Mapping | None) -> RoleAnswers:
    """Build step 7's answers from the draft's ``roles`` block.

    A role with no stored list keeps the installer's default for it, so the first render offers the
    names the installer would have offered.
    """
    data = data if isinstance(data, Mapping) else {}
    stored = data.get("roles")
    stored = stored if isinstance(stored, Mapping) else {}
    roles: dict[str, tuple[str, ...]] = {}
    for name, default in zip(DEFAULT_ROLE_NAMES, DEFAULT_ROLE_DEFAULTS):
        values = stored.get(name)
        if isinstance(values, (list, tuple)) and values:
            roles[name] = tuple(str(value) for value in values)
        else:
            roles[name] = (default,)
    return RoleAnswers(roles=roles,
                       audit_channel=str(data.get("audit_channel") or "").strip(),
                       admin_channel=str(data.get("admin_channel") or "").strip(),
                       central_admin=bool(data.get("central_admin", True)))


def non_secret_document(answers: BotAnswers) -> dict:
    """The draft-writable part of step 6's answers (the token is not part of it)."""
    return {
        "guild_id": answers.guild_id,
        "owner": answers.owner,
        "privileged_intents": bool(answers.privileged_intents),
    }


def roles_document(answers: RoleAnswers) -> dict:
    """The draft-writable part of step 7's answers."""
    return {
        "roles": {name: list(answers.roles.get(name, ())) for name in DEFAULT_ROLE_NAMES},
        "audit_channel": answers.audit_channel,
        "admin_channel": answers.admin_channel,
        "central_admin": bool(answers.central_admin),
    }


# ----------------------------------------------------------------------------------- the resolving

def resolve_bot(values: Mapping, previous: BotAnswers, *, validate: bool) -> tuple[BotAnswers | None,
                                                                                   tuple[str, dict] | None]:
    """Turn the submitted step 6 form into its answers.

    With ``validate`` set, an unusable answer returns ``(None, (code, data))`` — the classified
    failure that keeps the step open. Without it the answers are best-effort, which is what the
    progressive-enhancement re-render uses.
    """
    raw_guild = str(values.get("guild_id") or "").strip()
    guild_id = parse_id(raw_guild)
    if guild_id is None and validate:
        if not raw_guild:
            return None, ("discord_guild_required", {})
        return None, ("discord_guild_invalid", {})
    raw_owner = str(values.get("owner") or "").strip()
    owner = parse_id(raw_owner)
    if owner is None and validate:
        if not raw_owner:
            return None, ("discord_owner_required", {})
        return None, ("discord_owner_invalid", {})

    return BotAnswers(guild_id=guild_id or previous.guild_id,
                      owner=owner or previous.owner,
                      # the switch is the operator's "more than 10000 members" answer, inverted
                      privileged_intents=values.get("more_than_members") is None), None


def resolve_roles(values: Mapping, previous: RoleAnswers, *, validate: bool
                  ) -> tuple[RoleAnswers | None, tuple[str, dict] | None]:
    """Turn the submitted step 7 form into its answers.

    One row per internal role; the field accepts several values (the ``+ Add`` control) and each of
    them is split on commas, so the installer's comma form stays accepted. With ``validate`` set, an
    empty row or an unreadable channel id is the classified failure that keeps the step open.

    A form that carries no ``admin_scope`` reads as the central default, the same answer the step
    preselects: per-server is only ever taken from an explicit ``per_server`` value.
    """
    roles = {name: _split_roles(_entries(values, f"role_{index}"))
             for index, name in enumerate(DEFAULT_ROLE_NAMES)}
    answers = RoleAnswers(
        roles=roles,
        audit_channel=str(values.get("audit_channel") or "").strip(),
        admin_channel=str(values.get("admin_channel") or "").strip(),
        central_admin=str(values.get("admin_scope") or "central") == "central",
    )
    if not validate:
        return answers, None
    for name in DEFAULT_ROLE_NAMES:
        if not answers.roles[name]:
            return None, ("role_required", {"role": name})
    for field_name, text in (("audit", answers.audit_channel), ("admin", answers.admin_channel)):
        if text and parse_channel(text) is None:
            return None, ("channel_invalid", {"field": field_name})
    return answers, None


# ------------------------------------------------------------------------------- the config shapes

def main_block(answers: BotAnswers) -> dict:
    """The part of ``main.yaml`` step 6 owns: the guild id. ``autoupdate`` and ``language`` are
    step 1's and step 2's answers, and only the Review step assembles the whole file."""
    guild_id = answers.guild_id
    assert guild_id is not None            # a validated answer always carries one
    return {"guild_id": int(guild_id)}


def roles_block(answers: RoleAnswers) -> dict[str, list]:
    """The ``bot.yaml`` ``roles`` mapping: role name -> its role names and/or ids."""
    return {name: [_as_role(value) for value in answers.roles.get(name, ())]
            for name in DEFAULT_ROLE_NAMES}


def bot_block(bot: BotAnswers, roles: RoleAnswers) -> dict:
    """The ``services/bot.yaml`` body, built by the installer's own builder.

    An empty or ``-1`` channel is passed as the installer's "unset" id, and a per-server admin
    channel writes no central one at all.
    """
    audit = parse_channel(roles.audit_channel)
    admin = parse_channel(roles.admin_channel) if roles.central_admin else None
    owner = bot.owner
    assert owner is not None               # a validated answer always carries one
    return setup_config.build_discord_bot(
        owner=int(owner),
        privileged_intents=bool(bot.privileged_intents),
        roles=roles_block(roles),
        audit_channel=-1 if audit is None else audit,
        admin_channel=-1 if admin is None else admin,
    )
