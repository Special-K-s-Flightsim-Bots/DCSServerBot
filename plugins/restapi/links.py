"""JWT keys that reach only the servers of one DCS Admin (``auth.jwt.links``).

``auth.jwt.key`` reaches everything. A key in ``auth.jwt.links`` names its ``owner`` (a Discord user
id) and reaches the servers that member may manage as a DCS Admin, resolved on every request from
their current roles: the servers whose ``managed_by`` names one of their roles, plus the servers
without ``managed_by``. It may only call :data:`SCOPED_ENDPOINTS`, each for one server in reach.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from core.utils.discord import check_roles, holds_admin_role, may_manage_server

#: GET endpoints a scoped key may call, each answering for the one server in ``server_name``;
#: ``servers`` lists only the servers in reach.
SCOPED_ENDPOINTS = frozenset({
    "servers", "airbases", "airbase", "airbase/atis", "airbase/warehouse", "mission/group/waypoints",
    "mission/group", "mission/groups", "mission/bullseyes", "mission/drawings", "mission/unit",
})


@dataclass(frozen=True)
class Link:
    key: str
    owner: int | None = None     # None: the bot-wide auth.jwt.key


def parse_links(jwt_config: dict | None) -> list[Link]:
    jwt_config = jwt_config or {}
    links = [Link(str(jwt_config['key']))] if jwt_config.get('key') else []
    for entry in jwt_config.get('links') or []:
        if entry.get('key') and str(entry.get('owner', '')).isdigit():
            links.append(Link(str(entry['key']), int(entry['owner'])))
    return links


def match_key(links: list[Link], key: Any) -> Link | None:
    if not isinstance(key, str):
        return None
    return next((link for link in links if hmac.compare_digest(link.key.encode(), key.encode())), None)


def member_reach(bot: Any, member: Any) -> frozenset[str] | None:
    """The servers *member* may link: ``None`` = all (Admin or bot owner), else their DCS Admin servers."""
    if member is None:
        return frozenset()
    if (holds_admin_role(member, bot.roles['Admin']) or
            (member.id == bot.owner_id and not bot.node.locals.get('restrict_owner', False))):
        return None
    if not check_roles(bot.roles['DCS Admin'], member):
        return frozenset()
    return frozenset(name for name, server in bot.servers.items()
                     if may_manage_server(server.locals.get('managed_by'), member, is_admin=True,
                                          admin_roles=bot.roles['Admin']))


async def link_reach(bot: Any, link: Link) -> frozenset[str] | None:
    """What a key reaches: ``None`` = all (the bot-wide key). A scoped key is always a set, so it stays
    on :data:`SCOPED_ENDPOINTS` even if its owner becomes Admin."""
    if link.owner is None:
        return None
    reach = member_reach(bot, await bot.get_member(link.owner))
    return frozenset(bot.servers) if reach is None else reach


def enforce_reach(request: Request, endpoint: str, reach: frozenset[str] | None) -> None:
    """403 outside :data:`SCOPED_ENDPOINTS`; 404 (as for a missing server) for a server out of reach."""
    if reach is None:
        return
    if endpoint not in SCOPED_ENDPOINTS:
        raise HTTPException(status_code=403, detail="This key is limited to its owner's servers.")
    names = request.query_params.getlist('server_name')
    if endpoint == 'servers' and not names:
        return
    if len(names) != 1 or names[0] not in reach:
        raise HTTPException(status_code=404, detail="Server not found.")
