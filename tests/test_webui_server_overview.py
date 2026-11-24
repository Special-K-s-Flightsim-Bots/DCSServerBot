"""The SERVER PAGE'S OVERVIEW TAB: the server-status embed's facts, in the tab the operator was looking at.

and follow the link, you get multiple tabs. The first tab reads 'Overview' and it shows near to
nothing. I was talking about that one."* The standalone page and its nav item are GONE; its substance
now lives in the server page's first tab, which mirrors the bot's server-status embed's FIELD SELECTION
(the map, temperature, cloud preset, wind and runtime) and reads it from the ONE per-server read model
(``readmodels.servers.server_view``) the rest of the console already renders.

What this suite drives end to end (through the real app and the real local login backend, over a stub
cluster): the Overview tab renders a server's facts (status, mission, map, mission clock, seats,
player count, runtime, restart, the full weather block, the client limits, the extensions and the
server's schedule); a not-running server says so instead of printing zeroes; the embed-parity drift
test (the tab cannot silently fall behind the embed); the preset PICTURE, served by the page's own
read route out of the DCS installation; the standalone page is really gone; and a user-supplied mission
name containing ``<`` is never rendered as raw HTML.

WHAT THE PARITY TEST DOES **NOT** COVER, stated so a green run is not misread: the Ruler spacers
(``_ _``) are layout rather than a fact, and the embed's AUTHOR line, description and footer (its
server name, mission name, player count, DCS/bot versions and timestamp) are not ``add_field`` fields
at all, so the static field scan does not see them; they are covered by the rendered-tab assertions
below. Dynamic field names (the extensions' own names) are counted, not read.

WHAT THE LITERAL-NAME PARITY TEST CANNOT SEE, AND THE TAB SHOWS INSTEAD: the embed's
DYNAMICALLY-NAMED per-server block (``status.All`` / ``status.ServerInfo``) carries ``IP/Port``, a
``Restart`` countdown and the password-lock glyph under names built at runtime. The tab shows all three
— asserted by :func:`test_the_tab_shows_ip_port_the_password_lock_and_the_restart_countdown` — so they
are not left as an undeclared gap; only the password VALUE is deliberately not rendered.
"""
from __future__ import annotations

import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core import Status
from services.webservice import dcs_install
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels.servers import server_url
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, sign_in,
)

SERVER = "SRS-1"

#: the DCS installation root a node reports (``locals.DCS.installation``) — a real directory per test
INSTALL = "/dcs-world"

#: the preset picture's DCS-installation-relative name, exactly as ``clouds.preset.thumbnailName``
#: reports it (the one the API documents for a named preset: ``Bazar/Effects/Clouds/Thumbnails/…``)
THUMBNAIL = "Bazar/Effects/Clouds/Thumbnails/cloud_2.png"

#: a weather block and a clouds block shaped exactly as ``core/data/mission.py`` reports them.
WEATHER = {"season": {"temperature": 18.2},
           "qnh": 760.0,
           "visibility": {"distance": 10000},
           "wind": {"atGround": {"dir": 180, "speed": 5.0},
                    "at2000": {"dir": 190, "speed": 10.0},
                    "at8000": {"dir": 200, "speed": 20.0}}}
CLOUDS = {"preset": {"readableName": "Preset/Fair Weather", "thumbnailName": THUMBNAIL},
          "base": 1000}
#: DYNAMIC weather: no preset block at all, so the density and thickness are what the embed prints
DYNAMIC_CLOUDS = {"base": 1000, "density": 4, "thickness": 500}


# --------------------------------------------------------------------------------- test doubles

def mission(name, *, seconds=0, map_name="Caucasus", weather=None, clouds=None, date=None,
            start_time=0, slots_blue=0, slots_red=0):
    return types.SimpleNamespace(name=name, display_name=name, mission_time=seconds, map=map_name,
                                 weather=weather, clouds=clouds, date=date, start_time=start_time,
                                 num_slots_blue=slots_blue, num_slots_red=slots_red)


def server(name, *, status=Status.RUNNING, mission_obj=None, players=0,
           node_name="dcs-a-01", instance="Host-A-Instance", port=10309, max_players=16,
           display_name=None, managed_by=None, maintenance=False, settings=None):
    crew = {f"P{i}": types.SimpleNamespace(active=True, name=f"P{i}") for i in range(players)}
    return types.SimpleNamespace(
        name=name, display_name=display_name or name, status=status,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance), port=port,
        current_mission=mission_obj, players=crew, is_remote=False,
        settings=settings if settings is not None else {"maxPlayers": max_players},
        maintenance=maintenance,
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )


class PresetNode:
    """A node double carrying a DCS installation and reading files OUT of it.

    ``read_file`` records every path it was asked for, so a test can assert not only the bytes the
    route handed back but the FILE it resolved — and that a refused name was never opened at all.
    """

    def __init__(self, name="dcs-a-01", *, master=True, installation=INSTALL, error=None):
        self.name = name
        self.master = master
        self.instances: dict = {}
        self.plugins: list = []
        self.locals = {"DCS": {"installation": installation}} if installation else {}
        self.error = error
        self.reads: list[str] = []

    async def read_file(self, path):
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        with open(path, "rb") as handle:
            return handle.read()


def cluster(servers, *, nodes=()):
    node_objs = {name: types.SimpleNamespace(name=name, master=(i == 0), instances={})
                 for i, name in enumerate(nodes)}
    primary = next(iter(node_objs.values()), None)
    return Cluster(list(servers), nodes=node_objs, node=primary)


def cluster_on(servers, node):
    """The cluster a picture test needs: ONE node double, so the route resolves its installation."""
    return Cluster(list(servers), nodes={node.name: node}, node=node)


#: the zone the default plan declares …
BERLIN = "Europe/Berlin"
#: … and one whose offset never changes (no DST), so a card printing a constant is caught
TOKYO = "Asia/Tokyo"


def schedule_label(zone_name: str) -> str:
    """The schedule card's own timezone label for ``zone_name`` at the moment the page formats it.

    The card prints the plan's LIVE UTC offset (``Time (UTC%(offset)s)``) and drops the offset for
    UTC itself, so the expectation here is DERIVED from the declared zone: a written-down offset
    matches only half the year, while this reading follows the zone's DST rule.
    """
    offset = datetime.now().astimezone().astimezone(ZoneInfo(zone_name)).utcoffset()
    hours, remainder = divmod(int(offset.total_seconds()) if offset is not None else 0, 3600)
    minutes = abs(int(remainder)) // 60
    if hours == 0 and minutes == 0:
        return "Time (UTC)"
    sign = "+" if hours >= 0 else "-"
    return f"Time (UTC{sign}{abs(hours):02d}:{minutes:02d})"


class SchedulerStub:
    """The Scheduler cog's own ``get_config`` shape — the ONE thing the schedule card reads."""

    def __init__(self, config=None):
        self.config = config if config is not None else {"timezone": BERLIN,
                                                         "schedule": {"08-22": "YYYYYYN",
                                                                      "22-08": "NNNNNNY"}}

    def get_config(self, server=None, **kwargs):
        return self.config


@pytest.fixture
def scheduler():
    """Install a bot whose Scheduler cog answers — released again afterwards.

    The schedule card reads a RUNTIME object (``BotService.bot.cogs``) exactly as the auth layer reads
    the bot itself, so the fixture installs one into the service registry for the request's lifetime.
    """
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    bot = types.SimpleNamespace(cogs={"Scheduler": SchedulerStub()})
    # the NODE hangs off the SERVICE (``Service.__init__`` sets it), never off the discord client: the
    # console's own version is read from there, exactly as the auth layer reads the bot itself.
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
        bot=bot, node=types.SimpleNamespace(bot_version="3.8", sub_version=2))
    try:
        yield bot
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


async def open_tab(app, name=SERVER, tab="overview", user="specialk"):
    """Sign in and return the rendered server page for *name* on *tab* (default: the first tab)."""
    async with client(app) as http:
        await sign_in(http, user)
        response = await http.get(server_url(name, tab=tab))
        assert response.status_code == 200, f"{name}?tab={tab} answered {response.status_code}"
        return response.text


@pytest.fixture(autouse=True)
def fresh_overview_caches():
    """Empty the two cached RPCs the Overview tab owns before every test.

    They are ``cache_with_expiration``-decorated and keyed by the server's NAME — correct in a running
    console, where one long-lived server object carries a name — so in a suite every test would
    otherwise read the first test's answer for ``SRS-1`` (a 60 s / 300 s window outliving the test).
    """
    for cached in (detail_page._extension_views, detail_page._live_visibility):
        getattr(cached, "cache_clear")()
    yield


# ------------------------------------------------------------- 1. the tab renders the facts

@pytest.mark.asyncio
async def test_the_overview_tab_renders_the_server_facts(config_dir, clean_registry):
    """A running server's first tab carries the status word, the mission, the map, the player count,
    the runtime and the WHOLE weather block — the embed's own field set, each figure converted and
    rounded exactly as the embed's element does."""
    a = server(SERVER, mission_obj=mission("Rolling Thunder", seconds=3600,
                                           map_name="Caucasus", weather=WEATHER, clouds=CLOUDS,
                                           slots_blue=20, slots_red=10),
               players=3)
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "Rolling Thunder" in html, "the mission name is missing"
    assert "Caucasus" in html, "the tab must show the map"
    assert "1h0m" in html, "the runtime/label must render"
    assert "18.2 °C" in html, "the temperature ties to the embed's WeatherInfo"
    assert "10 kts" in html, "the surface wind ties to the embed's WeatherInfo"
    assert "Fair Weather" in html, "the cloud preset ties to the embed's WeatherInfo"
    assert "RUNNING" in html, "the status word is the read model's own vocabulary"
    # the player count (the tab's own line) and the tab heading
    assert "3 player(s)" in html
    assert ">Overview</h3>" in html, "the first tab is the Overview tab"
    # the embed's altimeter setting and cloud base, in the embed's own two units
    assert "1013 hPa / 29.92 inHg" in html, "the QNH pair is the embed's WeatherInfo"
    assert "3,281 ft" in html, "the cloud base is the embed's converted feet figure"
    # the visibility pair and both upper wind levels (the embed prints all three levels)
    assert "10,000 m / 6.21 SM" in html, "the visibility pair is the embed's own rendering"
    assert "6,600 ft" in html and "26,000 ft" in html, "the two upper wind levels are missing"
    # THE SEATS, per side, and the client-integrity policy the embed lists under Client Limits
    assert "0 / 20" in html and "0 / 10" in html, "the per-side seat counts are missing"
    # EVERY anti-cheat switch, with its state: the integrity flags and the data-export switches
    for label in ("No Trial Clients", "Pure Clients Required", "Pure Scripts Required",
                  "Pure Models Required", "Pure Textures Required", "Ownship export",
                  "Object export", "Sensor export"):
        assert label in html, f"the anti-cheat switch '{label}' is not listed"
    assert "✅" in html and "❌" in html, "the switches carry no state mark"
    # the two families are labelled, because a tick means the opposite in each
    assert "Integrity" in html and "Exports" in html
    # the mission clock (no date on this double: the label is absent, the runtime/restart pair is not)
    assert "Runtime</b>" in html or "Runtime:" in html


@pytest.mark.asyncio
async def test_a_not_running_server_says_so_instead_of_printing_zeroes(config_dir, clean_registry):
    """A STOPPED server has no mission and no weather; the tab renders the honest sentence, never a
    blank block or a fabricated ``0s`` runtime."""
    stopped = server(SERVER, status=Status.STOPPED, mission_obj=None)
    html = await open_tab(build(config_dir, cluster([stopped], nodes=("dcs-a-01",))))
    # the read model's own sentence (translated as the msgid under English)
    assert "This server is not running." in html
    assert "STOPPED" in html


# ------------------------------------------------------------- 2. the embed-parity drift test

def test_the_embed_builder_field_selection_is_exactly_what_the_tab_covers():
    """The embed's LITERAL add_field names must equal the covered set plus the documented omissions.

    The embed builder is ``plugins/mission/status.py`` (the element list is
    ``reports/serverStatus.json``). If the embed gains a field, this fails until the tab shows it
    (``EMBED_FIELD_COVERAGE``) or the omission is added to ``EMBED_FIELDS_NOT_COVERED`` with a reason.

    DOES NOT COVER: the two silently-dynamic field names (the ``ExtensionsInfo`` extension name and
    the ``ScheduleInfo`` ruler header) — counted, not read, because a static scan cannot resolve them;
    and the embed's non-field content (its author line, description and footer), which the tab mirrors
    as its title / mission / player lines and is asserted by the rendered tests above.
    """
    labels, dynamic = detail_page.embed_field_selection()
    declared = set(detail_page.EMBED_FIELD_COVERAGE) | set(detail_page.EMBED_FIELDS_NOT_COVERED)
    assert set(labels) == declared, (
        "the embed's field selection drifted from the Overview tab's declaration: "
        f"embed-only={set(labels) - declared}, tab-only={declared - set(labels)}")
    # the dynamically-named calls the scan cannot resolve (extensions + the schedule ruler)
    assert dynamic >= 2, f"expected dynamic add_field names, saw {dynamic}"


@pytest.mark.asyncio
async def test_every_covered_embed_field_actually_reaches_the_tab(config_dir, clean_registry, scheduler):
    """The covered labels are not just declared — a running server's tab renders each one, so the
    declaration cannot pass while the tab dropped a fact.

    The scheduler fixture is REQUIRED, not incidental: the schedule card renders only when the
    installation has a scheduler with a real plan (the embed takes the same branch), so without it the
    coverage would be asserted against a page that could not carry two of the labels at all.
    """
    a = server(SERVER, mission_obj=mission("M", seconds=120, map_name="Caucasus",
                                           weather=WEATHER, clouds=CLOUDS,
                                           date=datetime(2026, 10, 21), start_time=60))
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    for label in set(detail_page.EMBED_FIELD_COVERAGE.values()):
        assert label in html, f"the embed field '{label}' is declared covered but not rendered"


@pytest.mark.asyncio
async def test_the_omitted_spacer_is_the_only_thing_left_out():
    """The one declared omission is the Ruler spacer, and it is a LABEL the embed appends to every
    element — so the omission map cannot quietly grow a real fact again."""
    assert set(detail_page.EMBED_FIELDS_NOT_COVERED) == {"_ _"}


def test_the_parser_reads_literals_and_counts_dynamic_names():
    """Anti-vacuity for the drift test: the parser must SEE real fields, not return nothing."""
    labels, dynamic = detail_page.embed_field_selection()
    assert "Map" in labels and "Temperature" in labels and "Visibility" in labels
    assert dynamic >= 2


# ------------------------------------------------------------- 3. user data is never raw HTML

@pytest.mark.asyncio
async def test_a_mission_name_with_a_less_than_sign_is_escaped(config_dir, clean_registry):
    """A mission name is USER-SUPPLIED operator data: a ``<`` in it must reach the browser escaped,
    never as markup."""
    hostile = "<script>alert(1)</script>"
    a = server(SERVER, mission_obj=mission(hostile, weather=WEATHER, clouds=CLOUDS))
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    assert "<script>alert(1)</script>" not in html, "the mission name was rendered as raw HTML"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html, "the mission name was not escaped"


# ------------------------------------------------------------- 4. the tab renders in a language

@pytest.mark.asyncio
async def test_the_overview_tab_renders_in_a_chosen_language(config_dir, clean_registry, scheduler):
    """The tab's copy is TRANSLATED, not just present: a German reader gets German field labels,
    through the same per-language environment every page uses.

    The scheduler fixture makes the schedule card render, so the timezone label's own translation is
    checked too (a card that is absent would pass a presence-only assertion vacuously).
    """
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    app = build(config_dir, cluster([a], nodes=("dcs-a-01",)))
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(server_url(SERVER), headers={"Accept-Language": "de"})
        assert response.status_code == 200
        html = response.text
    assert "Karte" in html and "Laufzeit" in html, "the field labels must be translated"
    assert "Aktuelle Mission:" in html, "the mission line must be translated"
    # the fields this change ADDED are translated too — through the COMPILED catalog, which is the
    # half a `.po`-only check cannot see (gettext reads the `.mo`).
    for german in ("Sichtweite", "Wolkenuntergrenze", "QNH (QFF)", "Zeitplan", "Zeit (UTC"):
        assert german in html, f"the new label '{german}' is not translated"
    assert "Visibility" not in html and "Cloudbase" not in html, "an English label leaked through"


# ------------------------------------------------------------- 5. the standalone page is GONE

def test_the_standalone_overview_page_and_its_nav_item_are_gone():
    """The route, its capability and its nav item are removed; the read model it used is kept (the tab
    renders it). This is the guard that a leftover "/overview" door or menu entry cannot creep back."""
    from conftest import create_app, install_shell

    app = create_app()
    registrar = install_shell(app, node=None, config={})
    assert registrar.capability_for(path="/overview") is None, "a /overview route is still declared"
    assert "/overview" not in registrar.page_paths
    labels = {item.label for item in registrar.nav}
    assert "Server overview" not in labels, "the 'Server overview' menu item is still there"


# ------------------------------------------------------------- 6. the embed's per-server block

@pytest.mark.asyncio
async def test_the_tab_shows_ip_port_the_password_lock_and_the_restart_countdown(config_dir,
                                                                                 clean_registry):
    """The embed's DYNAMICALLY-NAMED per-server block carries IP/port, a password LOCK indicator and a
    restart countdown; the tab shows all THREE — and never the password VALUE itself."""
    from datetime import datetime, timedelta, timezone

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.node = types.SimpleNamespace(name="dcs-a-01", master=True, public_ip="203.0.113.7")
    a.settings = {"maxPlayers": 16, "password": "hunter2"}
    a.restart_time = datetime.now(timezone.utc) + timedelta(hours=2)

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "203.0.113.7:10309" in html, "the embed's IP/port is not shown"
    assert "hunter2" not in html, "the password VALUE must never be rendered"
    assert "🔐" in html, "the password-lock indicator is missing"
    assert "Restart:" in html and "hour" in html, "the restart countdown is not shown"


@pytest.mark.asyncio
async def test_the_lock_indicator_and_the_ip_are_absent_when_the_server_declares_neither(
        config_dir, clean_registry):
    """The indicator is a FACT, not decoration: a server with no password shows no lock, and a node
    with no public IP shows no address (only the bare port), never a placeholder."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    assert "🔐" not in html, "a lock was drawn for a server with no password"
    assert ":10309" in html, "the bare port is still shown"
    assert "203.0.113.7" not in html


# ------------------------------------------------------------- 7. the preset picture and its route

#: one backslash, by codepoint — a hostile name may use either separator, and a literal backslash in
#: this file would read as an escape to anyone skimming the list.
_BACKSLASH = chr(92)

#: names a MISSION FILE could report that must never resolve to a file outside the installation. Each
#: one is refused on its SHAPE: absolute POSIX, a Windows drive, a UNC share, a traversal, and a name
#: that is simply not a picture.
HOSTILE_NAMES = [
    "/etc/passwd.png",
    "../../../etc/passwd.png",
    "Bazar/../../../etc/passwd.png",
    "C:/Windows/Web/Wallpaper.png",
    f"{_BACKSLASH}{_BACKSLASH}server{_BACKSLASH}share{_BACKSLASH}cloud.png",
    f"{_BACKSLASH}etc{_BACKSLASH}passwd.png",
    "Bazar/Effects/Clouds/clouds.lua",
    "Bazar/Effects/Thumbnails/../secret.png",
]


def _picture_cluster(server_obj, tmp_path, thumbnail=THUMBNAIL):
    """A cluster whose single node reports *tmp_path* as its DCS installation."""
    node = PresetNode(installation=str(tmp_path))
    server_obj.current_mission.clouds = {"preset": {"readableName": "Preset/Fair Weather",
                                                    "thumbnailName": thumbnail}, "base": 1000}
    return cluster_on([server_obj], node), node


async def _fetch_picture(app, name=SERVER, user="specialk"):
    """``(the picture response, the rendered page)`` — the card and the route it points at."""
    async with client(app) as http:
        await sign_in(http, user)
        page = await http.get(server_url(name))
        response = await http.get(detail_page.preset_image_path(name))
        return response, page.text


def _write_thumbnail(tmp_path, name=THUMBNAIL, data=b"\x89PNG\r\n\x1a\npreset-picture"):
    target = Path(str(tmp_path)) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


@pytest.mark.asyncio
async def test_the_preset_picture_is_served_out_of_the_nodes_installation(config_dir, clean_registry,
                                                                         tmp_path):
    """The picture comes from the GAME host's installation, through this console's own route.

    The card carries the route URL (never a host path — one would 404 in the browser and leak where
    the operator keeps DCS), and the route resolves the mission's own ``thumbnailName`` against the
    node's ``locals.DCS.installation`` and hands the bytes back with the picture's own content type.
    """
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path)
    data = b"\x89PNG\r\n\x1a\npreset-picture"
    _write_thumbnail(tmp_path, data=data)

    response, page = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == detail_page.PRESET_IMAGE_MEDIA_TYPE
    assert response.content == data, "the bytes served are not the file's own"
    assert node.reads == [f"{Path(tmp_path).as_posix()}/{THUMBNAIL}"], \
        "the file was not resolved inside the install"
    assert f'src="{detail_page.preset_image_path(SERVER)}"' in page, "the card does not point at it"
    assert str(tmp_path) not in page, "a host path reached the page"
    assert Path(tmp_path).as_posix() not in page, "a host path reached the page"


@pytest.mark.asyncio
async def test_a_dotted_step_inside_the_name_still_resolves(config_dir, clean_registry, tmp_path):
    """The refusal is on the SHAPE, not on a naive match: a mission naming ``./`` inside the path
    resolves normally — the traversal test below must not be passing because every odd name fails."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path,
                               thumbnail="Bazar/./Effects/Clouds/Thumbnails/cloud_2.png")
    _write_thumbnail(tmp_path)

    response, _ = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 200, response.text
    assert node.reads == [f"{Path(tmp_path).as_posix()}/{THUMBNAIL}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
async def test_the_picture_route_refuses_a_name_that_leaves_the_installation(
        config_dir, clean_registry, tmp_path, hostile):
    """A name that is not a plain relative PNG path is REFUSED, and the file is never opened.

    ``thumbnailName`` comes out of a MISSION FILE, so it is operator data: resolving it would make the
    route a general file reader over the DCS host (and, on a shared master, over the console's own
    tree). The assertion on ``node.reads`` is the point — a 400 that had already opened the path would
    look identical in the response.
    """
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path, thumbnail=hostile)

    response, page = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 400, response.text
    assert node.reads == [], f"a refused name was still resolved onto the node: {node.reads}"
    assert str(tmp_path) not in page and "passwd" not in page


@pytest.mark.asyncio
async def test_the_picture_route_says_so_when_the_file_is_not_there(config_dir, clean_registry,
                                                                    tmp_path):
    """A mission whose preset has no picture in this installation answers its own sentence — never a
    zero-byte 200 that renders as a broken thumbnail."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path)

    response, page = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 404
    assert "is not in this server's DCS installation" in response.text
    assert response.headers["content-type"].startswith("text/plain")
    # the card still OFFERS the picture: a render asks no node anything, so the page cannot know the
    # file is missing — the empty frame is what `preset-thumb.js` hides.
    assert f'src="{detail_page.preset_image_path(SERVER)}"' in page


@pytest.mark.asyncio
async def test_the_picture_route_refuses_one_above_its_cap(config_dir, clean_registry, tmp_path,
                                                           monkeypatch):
    """The cap is real, not decorative: above it the route refuses with the size and the path, rather
    than streaming an arbitrary binary out of the DCS installation."""
    monkeypatch.setattr(detail_page, "PRESET_IMAGE_MAX_BYTES", 4)
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path)
    _write_thumbnail(tmp_path, data=b"12345")

    response, _ = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 413
    assert "above the 4-byte limit" in response.text
    assert Path(tmp_path).as_posix() in response.text, response.text


def test_the_pages_preset_cap_is_the_object_dcs_install_defines():
    """ONE cap number: the page carries no literal of its own — it carries ``dcs_install``'s object.

    A second literal here would be a number a future change to the ONE cap in ``dcs_install`` could
    never reach (the two would only coincide by hand).
    """
    assert detail_page.PRESET_IMAGE_MAX_BYTES is dcs_install.PRESET_IMAGE_MAX_BYTES


@pytest.mark.asyncio
async def test_the_picture_route_refuses_at_the_cap_the_page_reads(config_dir, clean_registry,
                                                                  tmp_path, monkeypatch):
    """The number is enforced, and it is ``dcs_install``'s OWN object (asserted first): the page holds
    no second number that a change to the ONE cap could fail to reach."""
    assert detail_page.PRESET_IMAGE_MAX_BYTES is dcs_install.PRESET_IMAGE_MAX_BYTES
    monkeypatch.setattr(detail_page, "PRESET_IMAGE_MAX_BYTES", 4)
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    cl, node = _picture_cluster(a, tmp_path)
    _write_thumbnail(tmp_path, data=b"12345")

    response, _ = await _fetch_picture(build(config_dir, cl))

    assert response.status_code == 413, response.text
    assert "above the 4-byte limit" in response.text, response.text
    # the message states the node's own path, and dcs_install states a node path FORWARD-slashed
    assert Path(tmp_path).as_posix() in response.text, response.text


@pytest.mark.asyncio
async def test_the_picture_is_offered_only_when_the_mission_reports_one(config_dir, clean_registry,
                                                                        tmp_path):
    """No ``thumbnailName`` in the weather, no picture frame: the card shows the preset's word alone."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=DYNAMIC_CLOUDS))
    cl, node = _picture_cluster(a, tmp_path, thumbnail="")

    response, page = await _fetch_picture(build(config_dir, cl))

    assert '<img data-preset-thumb' not in page, "a frame was drawn for a preset with no picture"
    assert response.status_code == 400, "the route still answers something for a mission with none"


@pytest.mark.asyncio
async def test_a_hoster_cannot_read_another_hosters_picture(config_dir, clean_registry, tmp_path):
    """The route carries the PAGE's capability and its SCOPE: a manager of another node's server is
    refused exactly as it is on the page, so the picture is not a wider door than the card."""
    from test_webui_write_pause import fleet

    cl = fleet()
    for srv in cl.servers:
        srv.current_mission = mission(srv.name, weather=WEATHER, clouds=CLOUDS)
    cl.nodes["dcs-a-01"].locals = {"DCS": {"installation": str(tmp_path)}}

    response, _ = await _fetch_picture(build(config_dir, cl), name="SRS-1", user="hoster-b")

    assert response.status_code == 403, response.text


def test_the_picture_route_is_registered_and_carries_the_pages_capability(config_dir, clean_registry):
    """No new capability: the picture is gated with the server page's own ``servers.view``, and it is a
    non-page route (it carries no nav item)."""
    app = build(config_dir, cluster([]))
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    assert detail_page.PRESET_IMAGE_PATH in routes
    assert detail_page.PRESET_IMAGE_PATH not in registrar.page_paths
    assert registrar.capability_for(route=routes[detail_page.PRESET_IMAGE_PATH]) == \
        detail_page.SERVER_DETAIL_CAPABILITY


# ------------------------------------------------------------- 8. the extensions and the schedule

@pytest.mark.asyncio
async def test_the_extensions_card_lists_what_the_server_reports(config_dir, clean_registry):
    """The embed's ``ExtensionsInfo`` element, as a card: one entry per extension, its value and its
    version — read through the server's own ``render_extensions``."""
    async def render_extensions():
        return [{"name": "RealWeather", "value": "QNH 1013\nTemp 18 °C", "version": "1.2"},
                {"name": "Tacview", "value": "connected", "version": ""}]

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.render_extensions = render_extensions

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    # ONE card, one hairline-separated entry per extension — the same treatment as the Overview sections
    assert ">Extensions</h3>" in html, "the extensions are not in a card"
    assert "<b>RealWeather</b>" in html and "<b>Tacview</b>" in html
    assert '<span class="tag">v1.2</span>' in html, "the version chip is missing"
    # SIDE BY SIDE: one auto-fitting column per extension, the hairline BETWEEN the columns
    assert 'grid-template-columns:repeat(auto-fit, minmax(240px, 1fr))' in html, \
        "the extensions are not laid out as columns"
    assert ';border-left:1px solid var(--line)">' in html, \
        "the entries are not hairline-separated from each other"
    assert "QNH 1013" in html, "the extension's own value line is missing"


@pytest.mark.asyncio
async def test_an_extension_without_a_version_shows_no_version(config_dir, clean_registry):
    """An extension that reports no version fills the field with a placeholder ("n/a"), and printing
    "vn/a" on the tile is worse than printing nothing — the bot's own footer skips the same case."""
    async def render_extensions():
        return [{"name": "DCS Voice Chat", "value": "connected", "version": "n/a"}]

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.render_extensions = render_extensions

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "DCS Voice Chat" in html
    assert "n/a" not in html and "vn/a" not in html, "a missing version was printed as one"


# ------------------------------------------------------- 8b. an extension's value line, with links

def test_value_parts_splits_links_bold_and_italic():
    """The parser: a markdown link, a bare URL, ``**bold**``, ``__italic__`` and the text around
    them — with the trailing punctuation of a sentence-ending URL left OUT of the link, and ordinary
    prose (a stray ``*``, a snake_case key) left ALONE."""
    from services.webservice.readmodels.model import value_parts

    parts = value_parts("[Link](https://www.digitalkneeboardsimulator.com/)\nTacview RT feed enabled")
    assert [(p.text, p.href) for p in parts] == [
        ("Link", "https://www.digitalkneeboardsimulator.com/"),
        ("\nTacview RT feed enabled", ""),
    ]
    assert [(p.text, p.href) for p in value_parts("see https://example.com/x.")] == [
        ("see ", ""), ("https://example.com/x", "https://example.com/x"), (".", ""),
    ]
    # the tacview value line: the bold run is a bold part, the two bullet lines stay text
    tacview = value_parts("- 87.191.179.232:52674\n- Delay: 10 minutes\n**Remote Ctrl [52675]**")
    assert [(p.text, p.style) for p in tacview] == [
        ("- 87.191.179.232:52674\n- Delay: 10 minutes\n", ""),
        ("Remote Ctrl [52675]", "bold"),
    ]
    assert [(p.text, p.style) for p in value_parts("__just italic__")] == [("just italic", "italic")]
    # NOT spaced delimiters, and never snake_case: prose is not formatting
    assert [(p.text, p.style) for p in value_parts("2 * 3 * 4 and ** 5 **")] == \
        [("2 * 3 * 4 and ** 5 **", "")]
    assert [(p.text, p.style) for p in value_parts("allow_object_export = true")] == \
        [("allow_object_export = true", "")]
    # NOTHING but an absolute http(s) URL becomes a link — an href out of operator data may not carry
    # another scheme, and the value must survive as text.
    for hostile in ("[click](javascript:alert(1))", "javascript:alert(1)",
                    "[x](data:text/html,<b>)", "[y](file:///etc/passwd)"):
        assert all(not part.href for part in value_parts(hostile)), hostile


@pytest.mark.asyncio
async def test_an_extension_value_with_a_link_renders_a_pressable_link(config_dir, clean_registry):
    """A link in an extension's value line is pressable, and it opens in a new tab — the same rule
    every other external link in the console follows. The text around it still renders as text."""
    async def render_extensions():
        return [{"name": "Kneeboard",
                 "value": "[Link](https://www.digitalkneeboardsimulator.com/)\nTacview RT feed enabled",
                 "version": ""}]

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.render_extensions = render_extensions

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert ('<a href="https://www.digitalkneeboardsimulator.com/" target="_blank" '
            'rel="noopener noreferrer">Link</a>') in html, "the markdown link is not pressable"
    assert "Tacview RT feed enabled" in html, "the rest of the value line was lost"


@pytest.mark.asyncio
async def test_an_extension_value_renders_discord_formatting(config_dir, clean_registry):
    """An extension writes its value line in DISCORD's own formatting (Tacview prints
    ``**Remote Ctrl [52675]**``): the bold run renders as ``<strong>``, the bullet lines as text."""
    async def render_extensions():
        return [{"name": "Tacview",
                 "value": "- 87.191.179.232:52674\n- Delay: 10 minutes\n**Remote Ctrl [52675]**",
                 "version": ""}]

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.render_extensions = render_extensions

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "<strong>Remote Ctrl [52675]</strong>" in html, "the bold run is not bold"
    assert "**Remote Ctrl" not in html, "the raw asterisks were rendered"
    assert "- Delay: 10 minutes" in html, "the plain lines were lost"


@pytest.mark.asyncio
async def test_an_extension_value_can_never_become_markup(config_dir, clean_registry):
    """The value line is OPERATOR DATA (an extension writes it): anything that is not a recognised
    http(s) link is escaped text, so a hostile extension cannot inject a script or an href."""
    async def render_extensions():
        return [{"name": "Hostile",
                 "value": "<script>alert(1)</script> [click](javascript:alert(2))",
                 "version": ""}]

    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.render_extensions = render_extensions

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "<script>alert(1)</script>" not in html, "the value was rendered as markup"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html, "the value was not escaped"
    assert 'href="javascript' not in html, "a javascript: URL became an href"


@pytest.mark.asyncio
async def test_a_server_that_reports_no_extensions_has_no_extensions_card(config_dir, clean_registry):
    """The embed omits the field when a server reports nothing; so does the card — a heading over an
    empty list would read as a broken render."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "<h2>Extensions</h2>" not in html


@pytest.mark.asyncio
async def test_the_schedule_card_renders_the_servers_own_plan(config_dir, clean_registry, scheduler):
    """The embed's ``ScheduleInfo``: the timezone, one row per period and the day states — read through
    the scheduler plugin's own ``get_config``."""
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert ">Schedule</h4>" in html, "the plan is not a section of the overview card"
    assert "08-22" in html and "22-08" in html, "the plan's periods are missing"
    assert "✅" in html and "❌" in html, "the day states are missing"
    assert schedule_label(BERLIN) in html, "the plan's timezone is missing"
    assert "shuts down without players" in html, "the legend is missing"


@pytest.mark.asyncio
async def test_the_schedule_card_derives_its_label_from_the_configured_zone(config_dir,
                                                                           clean_registry):
    """The label follows the PLAN's zone, not a constant: three plans, three labels.

    ``Europe/Berlin`` shifts with DST, ``Asia/Tokyo`` never does and UTC prints no offset at all, so
    a card that ignored ``timezone`` and printed one fixed offset fails whichever zone it picks.
    """
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    plans = {zone: {"timezone": zone, "schedule": {"08-22": "YYYYYYN", "22-08": "NNNNNNY"}}
             for zone in (BERLIN, TOKYO, "UTC")}
    rendered: dict[str, str] = {}
    saved = ServiceRegistry._singletons.get(BotService)
    try:
        for zone, config in plans.items():
            ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
                bot=types.SimpleNamespace(cogs={"Scheduler": SchedulerStub(config)}),
                node=types.SimpleNamespace(bot_version="3.8", sub_version=2))
            a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
            rendered[zone] = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved

    for zone, html in rendered.items():
        label = schedule_label(zone)
        assert label in html, f"the {zone} plan is not labelled {label!r}"
        for other in set(plans) - {zone}:
            if schedule_label(other) != label:
                assert schedule_label(other) not in html, \
                    f"the {zone} plan carries {other}'s label"
    assert schedule_label(BERLIN) != schedule_label(TOKYO)
    assert schedule_label(TOKYO) != schedule_label("UTC")


@pytest.mark.asyncio
async def test_a_server_on_the_default_plan_has_no_schedule_card(config_dir, clean_registry):
    """``00-24`` = ``YYYYYYY`` is the scheduler's DEFAULT (always on): the embed omits it and the card
    follows, so an ordinary server does not carry a card that says nothing."""
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
        bot=types.SimpleNamespace(cogs={"Scheduler": SchedulerStub({"schedule": {"00-24": "YYYYYYY"}})}))
    try:
        a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
        html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved

    assert ">Schedule</h3>" not in html


@pytest.mark.asyncio
async def test_the_header_carries_the_embeds_footer_versions_and_timestamp(config_dir, clean_registry,
                                                                          scheduler):
    """The embed's FOOTER, as the header's meta line: the server's DCS build, the console's own
    DCSServerBot version and when this page read the state.

    It is the one place a version can be read, and both halves come from a DIFFERENT object (the
    server's node, the console's service) — so a wrong source renders an empty half, which is what the
    assertion on the pair pins.
    """
    a = server(SERVER, mission_obj=mission("M", weather=WEATHER, clouds=CLOUDS))
    a.node = types.SimpleNamespace(name="dcs-a-01", master=True, public_ip="203.0.113.7",
                                   dcs_version="2.9.24.19921")

    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))

    assert "DCS 2.9.24.19921" in html, "the server's own DCS build is missing"
    assert "DCSServerBot 3.8.2" in html, "the console's own version is missing"
    assert "Last updated:" in html and "UTC" in html


@pytest.mark.asyncio
async def test_the_mission_clock_and_the_coalition_row_come_from_the_mission(config_dir,
                                                                             clean_registry):
    """A dated mission prints the embed's own clock line, and a server with a ``coalitions:`` block
    prints "Coalitions: Yes" where the seat counts would otherwise go — the embed's two branches."""
    from datetime import datetime

    a = server(SERVER, mission_obj=mission("M", seconds=600, start_time=60,
                                           date=datetime(2026, 10, 21),
                                           weather=WEATHER, clouds=CLOUDS))
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    assert "Date / Time in Mission" in html
    assert "2026-10-21" in html, "the mission's own date is not printed"

    a.locals["coalitions"] = {"blue_role": 1}
    html = await open_tab(build(config_dir, cluster([a], nodes=("dcs-a-01",))))
    assert "Coalitions: Yes" in html
    assert "Slots" not in html, "the seat counts belong to the DCS-side branch only"
