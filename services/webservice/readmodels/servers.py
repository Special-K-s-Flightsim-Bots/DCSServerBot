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
from .model import (NO_SERVERS_MESSAGE, LimitView, ServerView, WeatherView, format_duration,
                    status_view)

__all__ = ["SERVERS_HREF_PREFIX", "server_url", "servers", "server_view", "format_visibility"]


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
    qnh_hpa, qnh_inhg = _qnh(weather)
    clouds = _mapping(safe(lambda: getattr(mission, "clouds", None), None))
    base, density, thickness = _cloudbase(clouds)
    return WeatherView(preset=_preset(mission), temperature=temperature,
                       wind=_wind_level(safe(lambda: weather["wind"]["atGround"], None)),
                       qnh_hpa=qnh_hpa, qnh_inhg=qnh_inhg,
                       qnh=f"{qnh_hpa} hPa / {qnh_inhg} inHg" if qnh_hpa else "",
                       cloudbase=base, density=density, thickness=thickness,
                       visibility=_visibility(weather),
                       wind_low=_wind_level(safe(lambda: weather["wind"]["at2000"], None)),
                       wind_high=_wind_level(safe(lambda: weather["wind"]["at8000"], None)),
                       thumbnail=_thumbnail(mission))


def _qnh(weather) -> tuple[str, str]:
    """``(hPa, inHg)`` for the mission's altimeter setting, or ``("", "")`` — the SAME two figures and
    the SAME rounding the embed's ``WeatherInfo`` prints (``const``'s conversion factors)."""
    from core import const
    mmhg = safe(lambda: float(weather["qnh"]))
    if mmhg is None:
        return "", ""
    return f"{int(mmhg * const.MMHG_IN_HPA)}", f"{mmhg * const.MMHG_IN_INHG:.2f}"


def _cloudbase(clouds) -> tuple[str, str, str]:
    """``(cloudbase, density, thickness)`` in feet — ``("", "", "")`` when the mission reports none.

    A mission's clouds block carries a ``base`` and, for DYNAMIC weather, a ``density`` and a
    ``thickness``; a named PRESET carries its own layer table instead, so density and thickness stay
    empty there — exactly as the embed renders the two cases.
    """
    from core import const
    metres = safe(lambda: float(clouds["base"]))
    if metres is None:
        return "", "", ""
    base = f"{int(metres * const.METER_IN_FEET + 0.5):,} ft"
    if "preset" in clouds:
        return base, "", ""
    density = safe(lambda: int(clouds["density"]))
    thick = safe(lambda: float(clouds["thickness"]))
    return (base,
            f"{density}" if density is not None else "",
            f"{int(thick * const.METER_IN_FEET + 0.5):,} ft" if thick is not None else "")


def format_visibility(distance: float | None) -> str:
    """A visibility in METRES as the embed prints it, or ``''`` for nothing to print.

    The SAME two renderings the embed uses: the measured pair (metres and statute miles with two
    decimals) below 30 km, and the capped ``10 km / 6 SM (+)`` form at or above it. One formatter, so
    a mission's own visibility and a live server's fog reading cannot be rendered two ways.
    """
    from core import const
    if distance is None:
        return ""
    if distance >= 30000:
        return "10 km / 6 SM (+)"
    return f"{int(distance):,} m / {distance / const.METERS_IN_SM:.2f} SM"


def _visibility(weather) -> str:
    """The mission's own visibility, or ``''`` — see :func:`format_visibility`."""
    return format_visibility(safe(lambda: float(weather["visibility"]["distance"])))


def _wind_level(branch) -> str:
    """One wind level as ``180° / 5 kts``, or ``''`` — the embed's own offset and conversion.

    ``dir`` is offset by 180° (the mission stores the direction the wind blows TOWARD, the embed
    reports the one it blows FROM) and the speed is converted m/s -> knots with the bot's constant,
    rounded the same way. Reading it here rather than in the template keeps one rounding for the
    embed and the card.
    """
    from core import const
    level = _mapping(branch)
    if not level:
        return ""
    direction = safe(lambda: int(level["dir"]) + 180)
    speed = safe(lambda: float(level["speed"]) * const.METER_PER_SECOND_IN_KNOTS + 0.5)
    if direction is None or speed is None:
        return ""
    return f"{direction % 360}° / {int(speed)} kts"


def _thumbnail(mission) -> str:
    """The preset's thumbnail path RELATIVE to the DCS installation, or ``''``.

    ``clouds.preset.thumbnailName`` (e.g. ``Bazar/Effects/Clouds/Thumbnails/cloud_2.png``) is the
    mission-editor picture of the cloud preset, shipped inside the DCS installation. It is read here
    as DATA: the picture is served by the server page's own thumbnail route, which resolves it on the
    server's node and refuses a name that leaves the installation directory.
    """
    clouds = _mapping(safe(lambda: getattr(mission, "clouds", None), None))
    return text(safe(lambda: clouds["preset"]["thumbnailName"], ""))


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


def _mission_date(mission) -> str:
    """The mission clock the embed's "Date / Time in Mission" prints, or ``''``.

    ``Mission.date`` is the in-mission date and ``start_time`` the mission clock at its start; adding
    the elapsed ``mission_time`` gives the moment the mission has reached. ``date`` arrives as a
    ``datetime`` from one DCS build and as an ISO-ish string from another, so both shapes are
    rendered the way the embed renders them (a formatted local timestamp, or ``<date> <elapsed>``).
    """
    if mission is None:
        return ""
    from datetime import datetime, timedelta
    elapsed = as_int(attr(mission, "mission_time", 0)) or 0
    start = as_int(attr(mission, "start_time", 0)) or 0
    date = attr(mission, "date", None)
    if isinstance(date, datetime):
        return str(datetime.fromtimestamp(date.timestamp() + start + elapsed))
    if date is None or date == "":
        return ""
    return "{} {}".format(date, timedelta(seconds=start + elapsed))


def _slots(server, mission) -> tuple[str, str]:
    """``(blue, red)`` seats in use as ``"1 / 20"``, each ``''`` when the mission reports no count.

    The seat counts come from the mission's own ``num_slots_blue`` / ``num_slots_red`` and the players
    actually sitting on that side — the SAME two figures the embed's ``ServerInfo`` pairs up, read
    here off the player dictionary (``Player.side``) rather than by calling the server's own counter,
    which a remote server would answer over RPC.
    """
    if mission is None:
        return "", ""
    sides = {"Blue": 0, "Red": 0}
    for player in (attr(server, "players", {}) or {}).values():
        if not attr(player, "active", False):
            continue
        name = text(getattr(attr(player, "side", None), "name", "")).title()
        if name in sides:
            sides[name] += 1
    blue = ""
    total = as_int(attr(mission, "num_slots_blue", 0))
    if total:
        blue = f"{sides['Blue']} / {total}"
    red = ""
    total = as_int(attr(mission, "num_slots_red", 0))
    if total:
        red = f"{sides['Red']} / {total}"
    return blue, red


#: the client-integrity flags, as ``(label, settings key, path, default, in-force test)``. ``path``
#: is where the value lives (``settings`` itself or its ``advanced`` block), ``default`` is the value
#: the DCS config schema declares, and the test says which way round the flag reads: three of them
#: are "require …" (in force when TRUE) and trial clients are "allow …" (in force when FALSE).
_INTEGRITY_FLAGS: tuple[tuple[str, str, str, bool, bool], ...] = (
    (_("No Trial Clients"), "allow_trial_only_clients", "advanced", False, False),
    (_("Pure Clients Required"), "require_pure_clients", "", True, True),
    (_("Pure Scripts Required"), "require_pure_scripts", "", False, True),
    (_("Pure Models Required"), "require_pure_models", "", True, True),
    (_("Pure Textures Required"), "require_pure_textures", "", True, True),
)

#: the DCS DATA-EXPORT switches (``servers.yaml`` ``advanced:``), in force when TRUE. These are the
#: other half of anti-cheat: an external tool reads the mission through them, so they are shown with
#: the same ticks — under their own heading, because "on" means the opposite of what it means above.
_EXPORT_FLAGS: tuple[tuple[str, str], ...] = (
    (_("Ownship export"), "allow_ownship_export"),
    (_("Object export"), "allow_object_export"),
    (_("Sensor export"), "allow_sensor_export"),
)


def _restrictions(server) -> tuple[LimitView, ...]:
    """EVERY anti-cheat switch and whether it is in force — never only the set ones.

    The embed's "Client Limits" field prints just the integrity flags that are enabled, so a server
    with a hole in its protection and one that is fully closed read alike apart from a missing line.
    The card therefore carries the whole set, both families, each with its state. Values are read from
    ``settings`` (a dict, so not through ``attr``) and ``settings['advanced']``, with the DCS config
    schema's own defaults — the same defaults the scheduler's Anti-Cheat dialog shows.
    """
    settings = safe(lambda: getattr(server, "settings", None), None)
    if not isinstance(settings, dict):
        return ()
    advanced = _mapping(settings.get("advanced"))
    rows: list[LimitView] = []
    for label, key, path, default, true_is_in_force in _INTEGRITY_FLAGS:
        source = advanced if path == "advanced" else settings
        value = bool(source.get(key, default))
        # the labels are marked translatable WHERE THEY ARE DECLARED (above), so the extractor sees a
        # literal and a translated catalog entry can only be the flag it belongs to.
        rows.append(LimitView(label=label, enabled=value if true_is_in_force else not value,
                              kind="integrity"))
    for label, key in _EXPORT_FLAGS:
        rows.append(LimitView(label=label, enabled=bool(advanced.get(key, True)), kind="export"))
    return tuple(rows)


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
    slots_blue, slots_red = safe(lambda: _slots(server, mission), ("", "")) or ("", "")
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
        mission_date=safe(lambda: _mission_date(mission), "") or "",
        slots_blue=slots_blue,
        slots_red=slots_red,
        # a server with a `coalitions:` block assigns seats through its Discord roles, so the embed
        # prints "Coalitions: Yes" where the slot counts would otherwise go — read off the same
        # `locals` block the bot's own report reads.
        coalitions=bool(_mapping(safe(lambda: getattr(server, "locals", None), None)).get(
            "coalitions")),
        restrictions=safe(lambda: _restrictions(server), ()) or (),
        dcs_version=text(attr(attr(server, "node", None), "dcs_version", None)),
    )


def servers(source) -> list[ServerView]:
    """Every server of the source, sorted by name (stable output for readers and for tests)."""
    out: list[ServerView] = []
    for server in getattr(source, "servers", ()) or ():
        out.append(safe(lambda: server_view(server), None) or _blank())
    out.sort(key=lambda view: view.name)
    return out