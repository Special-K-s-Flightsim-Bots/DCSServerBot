"""Players online — read from the servers' in-process player dictionaries.

The bot holds every connected player on the server object (``Server.players``, keyed by UCID), with
``active`` marking the ones actually sitting in a slot. That is the authoritative "online right
now" set and it costs nothing to read; nothing is queried, awaited or asked of another node.

WHY THERE IS NO PING AND NO CONNECT TIME. ``Player`` carries no ping and no connect timestamp — the
DCS broadcast that fills the object has neither — so the mockup's *Ping* and *Connected* columns are
omitted rather than filled with a plausible-looking figure. Every number the page renders has a
source; those two have none. Restoring them is a read-model change (a heartbeat/last_seen column),
not a template change.
"""
from __future__ import annotations

from .access import attr, safe, text
from .model import NO_PLAYERS_MESSAGE, PlayerView

__all__ = ["players_online", "player_views"]

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_PLAYERS_MESSAGE


def _slot(player) -> str:
    """The seat a player occupies: ``A-10C II · Hog 1`` when both are known, else whichever is."""
    unit = text(attr(player, "unit_display_name", None)) or text(attr(player, "unit_type", None))
    group = text(attr(player, "group_name", None))
    if unit and group:
        return f"{unit} · {group}"
    return unit or group


def player_views(server_name: str, players) -> list[PlayerView]:
    """The active players of ONE server, sorted by name."""
    out: list[PlayerView] = []
    for player in (players or {}).values():
        if not attr(player, "active", False):
            continue
        out.append(PlayerView(
            name=text(attr(player, "name", None)),
            ucid=text(attr(player, "ucid", None)),
            server_name=server_name,
            slot=safe(lambda: _slot(player), "") or "",
            active=True,
        ))
    out.sort(key=lambda view: view.name)
    return out


def players_online(source) -> list[PlayerView]:
    """Every ACTIVE player of every server, sorted by server then name."""
    out: list[PlayerView] = []
    for server in getattr(source, "servers", ()) or ():
        server_name = text(attr(server, "name", None))
        out.extend(safe(lambda: player_views(server_name, attr(server, "players", {})), []) or [])
    out.sort(key=lambda view: (view.server_name, view.name))
    return out