"""Config-shape builders shared by the installer and the web setup tour.

Every builder returns the exact dict the installer writes to disk, key order included -- the YAML
round-trip preserves insertion order, so the order here is the order in the file. They take plain
values: no prompts, no terminal, no translation.
"""
from __future__ import annotations

import random
from typing import Any

__all__ = [
    "DEFAULT_ROLE_NAMES",
    "DEFAULT_NO_DISCORD_ROLES",
    "DEFAULT_ROLE_DEFAULTS",
    "SRS_CONFIG_TEMPLATE",
    "build_main",
    "build_no_discord_main",
    "build_discord_bot",
    "build_no_discord_bot",
    "build_node",
    "build_instance",
    "build_server_channels",
    "build_scheduler",
]

DEFAULT_ROLE_NAMES = ("Admin", "DCS Admin", "DCS")
DEFAULT_NO_DISCORD_ROLES = ("Admin", "DCS Admin")
DEFAULT_ROLE_DEFAULTS = ("Admin", "DCS Admin", "@everyone")
SRS_CONFIG_TEMPLATE = '{instance.home}/Config/SRS.cfg'


def build_main(guild_id: int, autoupdate: bool, language: str | None = None) -> dict[str, Any]:
    """The ``main.yaml`` body for a Discord install."""
    main: dict[str, Any] = {"guild_id": guild_id, "autoupdate": autoupdate}
    if language is not None:
        main['language'] = language
    return main


def build_no_discord_main(guild_name: str, autoupdate: bool,
                          guild_id: int | None = None) -> dict[str, Any]:
    """The ``main.yaml`` body for a non-Discord install. A random placeholder guild id is drawn
    when none is given."""
    if guild_id is None:
        guild_id = random.randint(111111111111111111, 999999999999999999)
    return {"guild_id": guild_id, "guild_name": guild_name, "autoupdate": autoupdate}


def build_discord_bot(owner: int, privileged_intents: bool, roles: dict[str, list],
                      audit_channel: int = -1, admin_channel: int = -1) -> dict[str, Any]:
    """The ``services/bot.yaml`` body for a Discord install. A channel id of ``-1`` means unset."""
    bot: dict[str, Any] = {"owner": owner, "privileged_intents": privileged_intents, "roles": roles}
    channels: dict[str, Any] = {}
    if audit_channel and audit_channel != -1:
        channels['audit'] = audit_channel
    if admin_channel and admin_channel != -1:
        channels['admin'] = admin_channel
    if channels:
        bot['channels'] = channels
    return bot


def build_no_discord_bot(roles: dict[str, list]) -> dict[str, Any]:
    """The ``services/bot.yaml`` body for a non-Discord install."""
    return {"no_discord": True, "roles": roles}


def build_node(listen_port: int, use_upnp: bool, *, public_ip: str | None = None,
               database_url: str | None = None, dcs_installation: str | None = None,
               dcs_autoupdate: bool = False,
               srs_installation: str | None = None) -> dict[str, Any]:
    """One ``nodes.yaml`` entry, without its instances.

    The ``database`` block is written only for the node that owns the connection (a master install);
    the ``DCS``/``extensions``/``instances`` blocks only when a DCS installation is set.
    """
    node: dict[str, Any] = {"listen_port": listen_port, "use_upnp": use_upnp}
    if public_ip:
        node['public_ip'] = public_ip
    if database_url is not None:
        node["database"] = {"url": database_url}
    if dcs_installation:
        dcs: dict[str, Any] = {"installation": dcs_installation}
        if dcs_autoupdate:
            dcs["autoupdate"] = True
        node["DCS"] = dcs
        if srs_installation:
            node['extensions'] = {'SRS': {'installation': srs_installation}}
        node['instances'] = {}
    return node


def build_instance(home: str, bot_port: int, *, srs_installation: str | None = None,
                   srs_port: int | None = None) -> dict[str, Any]:
    """One ``nodes.yaml`` instance entry."""
    instance: dict[str, Any] = {"bot_port": bot_port, "home": home}
    if srs_installation:
        instance['extensions'] = {"SRS": {"config": SRS_CONFIG_TEMPLATE, "port": srs_port}}
    return instance


def build_server_channels(status: int, chat: int = -1, admin: int | None = None) -> dict[str, Any]:
    """The ``servers.yaml`` channels block. The admin channel is written only when the bot has no
    central admin channel."""
    channels: dict[str, Any] = {"status": status, "chat": chat}
    if admin is not None:
        channels['admin'] = admin
    return channels


def build_scheduler(autostart: bool) -> dict[str, Any]:
    """One ``plugins/scheduler.yaml`` instance entry: an all-day schedule when the server is to be
    autostarted, an empty mapping otherwise."""
    if autostart:
        return {"schedule": {"00-24": "YYYYYYY"}}
    return {}
