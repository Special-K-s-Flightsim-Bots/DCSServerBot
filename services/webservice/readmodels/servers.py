"""Servers: the live registry the master holds (``bus.servers``), one row per server.

Each server carries its own status, port, current mission and player dictionary, so the whole card
is a pure read of objects already in this process — nothing is awaited, nothing is sent to a node,
and no Discord attribute is touched. The page therefore renders while the bot is still connecting
and after a takeover, when ``BotService.bot`` is ``None``.

The players-online view lives in :mod:`.players` (the same objects, the same tolerant accessors) —
one module per read model, so each figure has exactly one owner.
"""
from __future__ import annotations

from urllib.parse import quote

from .access import attr, as_int, port_number, safe, text
from .model import NO_SERVERS_MESSAGE, ServerView, status_view

__all__ = ["SERVERS_HREF_PREFIX", "server_url", "servers", "server_view"]

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_SERVERS_MESSAGE

#: the per-server page's path prefix — the SAME route ``pages/server_detail`` registers
#: (``SERVER_DETAIL_PATH``). This module owns a server's read model AND its URL, so every builder of
#: that URL (the row link, the config write's redirect back, the page's tab links) goes through
#: :func:`server_url` and the same name always yields the same URL.
SERVERS_HREF_PREFIX = "/servers/"


def server_url(name, *, tab: str = "") -> str:
    """The per-server page URL for *name* — the ONE encoding of a server name.

    ``quote(str(name), safe='')`` percent-encodes every reserved character a DCS server name may
    carry (a space, ``&``, ``#``, ``?``, ``/``), so a name round-trips through the URL segment
    instead of truncating it or opening a query string. ``tab`` names the tab to open on.
    """
    url = f"{SERVERS_HREF_PREFIX}{quote(str(name), safe='')}"
    return f"{url}?tab={tab}" if tab else url


def _mission(server) -> tuple[str | None, int]:
    """``(mission name, elapsed seconds)`` for the current mission, or ``(None, 0)``.

    ``Mission.mission_time`` is the DCS-reported mission clock and the one mission figure that is
    genuinely in-process — no wall-clock arithmetic, so it cannot drift from what the server says.
    """
    mission = attr(server, "current_mission", None)
    if mission is None:
        return None, 0
    name = text(attr(mission, "name", None)) or None
    seconds = as_int(attr(mission, "mission_time", 0)) or 0
    return name, max(seconds, 0)


def _instance_name(server) -> str | None:
    instance = attr(server, "instance", None)
    if instance is None:
        return None
    return text(attr(instance, "name", None)) or None


def _blank() -> ServerView:
    """A row for a server whose view could not be built — visible, never a hole in the table."""
    return ServerView(name="?", node_name="", instance_name=None, status=status_view(None),
                      port=None, mission_name=None, mission_seconds=0, players=0,
                      active_players=0, remote=False)


def server_view(server) -> ServerView:
    """One server row. Never raises: a half-initialised object renders as a partial row."""
    players = attr(server, "players", {}) or {}
    active = safe(lambda: len([p for p in players.values() if attr(p, "active", False)]), 0) or 0
    mission_name, mission_seconds = safe(lambda: _mission(server), (None, 0)) or (None, 0)
    return ServerView(
        name=text(attr(server, "name", None)),
        node_name=text(attr(attr(server, "node", None), "name", None)),
        instance_name=safe(lambda: _instance_name(server), None),
        status=status_view(attr(server, "status", None)),
        port=port_number(attr(server, "port", None)),
        mission_name=mission_name,
        mission_seconds=mission_seconds,
        players=len(players),
        active_players=active,
        remote=bool(attr(server, "is_remote", False)),
        maintenance=bool(attr(server, "maintenance", False)),
        link=server_url(text(attr(server, "name", None))),
    )


def servers(source) -> list[ServerView]:
    """Every server of the source, sorted by name (stable output for readers and for tests)."""
    out: list[ServerView] = []
    for server in getattr(source, "servers", ()) or ():
        out.append(safe(lambda: server_view(server), None) or _blank())
    out.sort(key=lambda view: view.name)
    return out