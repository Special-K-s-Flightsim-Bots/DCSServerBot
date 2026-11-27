"""JWT keys that reach only the servers of one DCS Admin (``auth.jwt.links``).

``auth.jwt.key`` reaches everything. A key in ``auth.jwt.links`` names its ``owner`` (a Discord user
id) and reaches the servers that member may manage, resolved on every request from their current
roles by the console's own rule: an Admin and the bot owner reach everything, a DCS Admin reaches the
servers whose ``managed_by`` names one of their roles plus the servers without ``managed_by``, and a
manager reaches the servers naming one of their roles. It may only call :data:`SCOPED_ENDPOINTS`,
each for one server in reach.

The reach is a :class:`services.webservice.scope.Scope`, never a hand-rolled comparison: which
servers an identity may manage is one rule with one home (the console's), so a scoped key cannot
answer it differently from the Discord commands or the admin website.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from starlette.requests import Request

from core.utils.discord import member_scope_tokens
from services.webservice.scope import CLUSTER_ROLES, Scope, scope_from_roles, server_in_scope

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


def held_role_keys(member: Any, role_map: dict | None) -> frozenset[str]:
    """Which of the bot's configured roles (*role_map*, ``bot.roles``) *member* holds, by KEY.

    The keys are the names the commands and the console use (``Admin``, ``DCS Admin``, …) while the
    values may be role ids and/or names, so the comparison goes through
    :func:`core.utils.discord.member_scope_tokens` — the same pair ``check_roles`` builds — and one
    installation that grants a role by id is not silently treated as roleless.
    """
    tokens = member_scope_tokens(member)
    return frozenset(key for key, configured in (role_map or {}).items()
                     if not tokens.isdisjoint(configured or ()))


def member_scope(bot: Any, member: Any) -> Scope:
    """The scope ONE member's link carries. Unresolvable member: :meth:`Scope.unresolved` (deny).

    The bot owner is unscoped unless ``restrict_owner``. A link is otherwise LICENSED by a CLUSTER
    role (``Admin`` / ``DCS Admin``) — Discord's commands grant the feature to those two — and
    ``managed_by`` then NARROWS what the role reaches, never admits on its own. The narrowing itself
    is the shared precedence (:func:`services.webservice.scope.scope_from_roles`): an Admin reaches
    everything, a DCS Admin the servers naming one of their roles plus the untagged ones.
    """
    if member is None:
        return Scope.unresolved()
    if member.id == getattr(bot, 'owner_id', None) and not bot.node.locals.get('restrict_owner', False):
        return Scope.everything()
    held = held_role_keys(member, getattr(bot, 'roles', None))
    if held.isdisjoint(CLUSTER_ROLES):
        return Scope.restricted(frozenset())
    return scope_from_roles(held, member_scope_tokens(member))


def scope_servers(bot: Any, scope: Scope) -> frozenset[str]:
    """The bot's servers a scope covers — the ONE answer the reach check and the listing read."""
    return frozenset(name for name, server in (getattr(bot, 'servers', None) or {}).items()
                     if server_in_scope(scope, server))


def member_reach(bot: Any, member: Any) -> frozenset[str] | None:
    """The servers *member* may link: ``None`` = all (Admin or the unrestricted owner)."""
    scope = member_scope(bot, member)
    return None if scope.unrestricted else scope_servers(bot, scope)


async def link_reach(bot: Any, link: Link) -> frozenset[str] | None:
    """What a key reaches: ``None`` = all (the bot-wide key). A scoped key is always a set, so it stays
    on :data:`SCOPED_ENDPOINTS` even if its owner has become Admin."""
    if link.owner is None:
        return None
    reach = member_reach(bot, await bot.get_member(link.owner))
    return frozenset(bot.servers) if reach is None else reach


def enforce_reach(request: Request, endpoint: str, reach: frozenset[str] | None,
                  resolve: Callable[[str], str] | None = None) -> None:
    """403 outside :data:`SCOPED_ENDPOINTS`; 404 (as for a missing server) for a server out of reach.

    *resolve* maps the request's ``server_name`` to a server name. The routes accept an INSTANCE
    name as an alias (``resolve_server_name``), so the reach check must resolve it the same way or a
    customer's own server answers 404 when they name it by its instance.
    """
    if reach is None:
        return
    if endpoint not in SCOPED_ENDPOINTS:
        raise HTTPException(status_code=403, detail="This key is limited to its owner's servers.")
    names = request.query_params.getlist('server_name')
    if endpoint == 'servers' and not names:
        return
    name = resolve(names[0]) if (len(names) == 1 and resolve) else names[0] if names else None
    if len(names) != 1 or name not in reach:
        raise HTTPException(status_code=404, detail="Server not found.")
