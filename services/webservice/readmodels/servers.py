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
from .model import (NO_SERVERS_MESSAGE, ServerView, WeatherView, format_duration, status_view)

__all__ = ["SERVERS_HREF_PREFIX", "server_url", "servers", "server_view"]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED (see model._)."""
    return message


#: the cloud/weather preset words the read model reports when the mission carries no named preset.
#: Marked translatable here (the card renders them through the per-language environment's ``_``),
#: mirroring the embed's own "Clear" / "Dynamic" branches (``plugins/mission/status.py``).
CLEAR_PRESET = _("Clear")
DYNAMIC_PRESET = _("Dynamic")

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_SERVERS_MESSAGE

#: the per-server page's path prefix — the SAME route ``pages/server_detail`` registers
#: (``SERVER_DETAIL_PATH``). This module owns a server's read model AND its URL, so every builder of
#: that URL (the row link, the config write's redirect back, the page's tab links) goes through
#: :func:`server_url` and the same name always yields the same URL.
SERVERS_HREF_PREFIX = "/fleet/"


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


def _mapping(value) -> dict:
    """*value* as a mapping, or ``{}`` — the weather/clouds blocks are DICTS, not attributes, so
    the tolerant accessors below read them with ``[]``/``.get`` under :func:`safe`."""
    return value if isinstance(value, dict) else {}


def _weather(server) -> WeatherView | None:
    """The server's weather as the card's three facts, or ``None`` when the mission carries none.

    Mirrors ``plugins/mission/status.WeatherInfo``: the PRESET comes from the mission's ``clouds``
    (a named preset, ``Clear`` when the density is 0, else ``Dynamic``), the TEMPERATURE from
    ``weather.season.temperature``, and the WIND from ``weather.wind.atGround`` — direction offset by
    180° and speed converted m/s -> knots with the bot's own constant, exactly as the embed does.
    Every read is tolerant: a missing block yields an empty field, and a mission with no weather at
    all yields ``None`` (the card then renders :data:`~.model.NO_WEATHER_MESSAGE`).
    """
    mission = attr(server, "current_mission", None)
    if mission is None:
        return None
    weather = _mapping(safe(lambda: getattr(mission, "weather", None), None))
    if not weather:
        return None
    temperature = ""
    degrees = safe(lambda: float(weather["season"]["temperature"]))
    if degrees is not None:
        temperature = f"{degrees:.1f} °C"
    wind = ""
    ground = _mapping(safe(lambda: weather["wind"]["atGround"], None))
    if ground:
        from core import const
        direction = safe(lambda: int(ground["dir"]) + 180)
        speed = safe(lambda: float(ground["speed"]) * const.METER_PER_SECOND_IN_KNOTS + 0.5)
        if direction is not None and speed is not None:
            wind = f"{direction % 360}° / {int(speed)} kts"
    return WeatherView(preset=_preset(mission), temperature=temperature, wind=wind)


def _preset(mission) -> str:
    """The cloud/weather preset word — a named preset, ``Clear`` or ``Dynamic`` (never blank for a
    mission that carries a clouds block). See :func:`_weather` for the source."""
    clouds = _mapping(safe(lambda: getattr(mission, "clouds", None), None))
    if not clouds:
        return ""
    readable = safe(lambda: clouds["preset"]["readableName"])
    if readable:
        # the embed takes the first line and normalises the '/'-separated names; keep that shape
        return str(readable).split("\n")[0].split("/")[-1].strip()
    if "density" in clouds and clouds.get("density") == 0:
        return CLEAR_PRESET
    return DYNAMIC_PRESET


def _max_players(server) -> int | None:
    """``settings.maxPlayers``, or ``None`` when the source carries no settings (a tolerant read —
    ``settings`` is a dict, so it is not reachable through :func:`~.access.attr`)."""
    settings = safe(lambda: getattr(server, "settings", None), None)
    if not isinstance(settings, dict):
        return None
    return as_int(settings.get("maxPlayers"))


def _public_ip(server) -> str:
    """The node's publicly reachable IP (``node.public_ip``), or ``''``.

    Read tolerantly (a node without the attribute, or without one, yields ``''``). The overview card
    pairs it with the row's ``port`` as ``ip:port``, mirroring the embed's per-server block.
    """
    node = attr(server, "node", None)
    return text(attr(node, "public_ip", None))


def _locked(server) -> bool:
    """Whether a password is required to join (``settings.password`` non-empty).

    The card renders the LOCK INDICATOR only — the password VALUE is never exposed (the web page has
    no ``show_passwords`` gate to honour, so it never discloses the cleartext), which is why this is
    a boolean rather than the string. ``settings`` is a dict, so it is not reachable through
    :func:`~.access.attr`; a source carrying no settings is simply not locked.
    """
    settings = safe(lambda: getattr(server, "settings", None), None)
    if not isinstance(settings, dict):
        return False
    return bool(settings.get("password"))


def _restart_in(server) -> str:
    """The time until the scheduled restart, formatted as the embed's per-server block does
    (``utils.format_time``), or ``''`` when no restart is scheduled or the server is in maintenance.

    Mirrors ``plugins/mission/status.All.render``: only a server with a ``restart_time`` and NOT in
    maintenance reports a countdown, and the figure is computed against the same UTC now. A restart
    in the past folds to ``''`` rather than a negative figure (``format_time(0)`` is empty).
    """
    if bool(attr(server, "maintenance", False)):
        return ""
    restart_time = attr(server, "restart_time", None)
    if restart_time is None:
        return ""
    from datetime import datetime, timezone

    from core import utils
    seconds = safe(lambda: int((restart_time - datetime.now(timezone.utc)).total_seconds()), None)
    if seconds is None:
        return ""
    return utils.format_time(max(seconds, 0))


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
    name = text(attr(server, "name", None))
    mission = attr(server, "current_mission", None)
    return ServerView(
        name=name,
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
        link=server_url(name),
        display_name=text(attr(server, "display_name", None)) or name,
        max_players=_max_players(server),
        map_name=(text(attr(mission, "map", None)) or None) if mission is not None else None,
        runtime=format_duration(mission_seconds),
        weather=safe(lambda: _weather(server), None),
        public_ip=safe(lambda: _public_ip(server), "") or "",
        locked=safe(lambda: _locked(server), False) or False,
        restart_in=safe(lambda: _restart_in(server), "") or "",
    )


def servers(source) -> list[ServerView]:
    """Every server of the source, sorted by name (stable output for readers and for tests)."""
    out: list[ServerView] = []
    for server in getattr(source, "servers", ()) or ():
        out.append(safe(lambda: server_view(server), None) or _blank())
    out.sort(key=lambda view: view.name)
    return out