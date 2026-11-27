"""The public site's fleet: the Servers list and the page one server opens.

WHAT THIS SUITE PINS

* the list is ONE strip per server of the site, ordered up first then by name, with the live half
  (state, players/max, mission, map, runtime, restart-in, lock, maintenance) on every row — and a
  server of another site is on neither page;
* the per-server page is the list's own path plus one encoded segment, carries the embed's
  inventory from the same read, and answers the page's own not-found state for an unknown name and
  for another site's server (never that server's figures);
* the two live DCS blocks (weather, extensions) render the CACHED read: a tile never prints a
  credential, a Discord markdown value is flattened into plain lines, the withheld count is a chip,
  and a server that is not up — or a read that failed — states that in one sentence, never zeros;
* the live read never waits on the fog query: ``getFog`` is a bounded enhancement of the visibility
  figure, a hung or raising one changes nothing else, a hung extensions render states its own
  failure beside the drawn weather, and every outcome — ready, down, failed — is cached;
* the read layer's new functions bind the site's server set into their one statement;
* the overview no longer carries a server band: the list, whole, is this page's.

Every page is rendered from the REAL templates through the suite's harness.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import types
from datetime import datetime, timedelta, timezone

import pytest

from conftest import REPO_ROOT, asgi_client
from core.data.const import Port, PortType
from core.utils.helper import ThreadSafeDict
from services.webservice import i18n, site_pages, site_reads
from services.webservice.pages import site_leaderboard, site_servers
from test_webui_site_pages import (  # noqa: F401 - fixtures are imported on purpose
    StubPool, build, bus, config_dir)


# --------------------------------------------------------------------------- the fleet

def _status(word: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=word)


def _node(**fields) -> types.SimpleNamespace:
    base = {"public_ip": "51.83.14.22", "dcs_version": "2.9.8.1108", "bot_version": "3.6.2"}
    base.update(fields)
    return types.SimpleNamespace(**base)


def _mission(name="Northern Shield D3", map_name="Syria", mission_time=8076, blue=16, red=8):
    return types.SimpleNamespace(
        name=name, map=map_name, mission_time=mission_time, start_time=9 * 3600 + 42 * 60 + 18,
        date=datetime(1987, 6, 14, 0, 0, 0),
        num_slots_blue=blue, num_slots_red=red,
        weather={"season": {"temperature": 24.6}, "qnh": 758.7,
                 "visibility": {"distance": 9400.0},
                 "wind": {"atGround": {"dir": 160, "speed": 4.1},
                          "at2000": {"dir": 175, "speed": 6.2},
                          "at8000": {"dir": 105, "speed": 17.5}}},
        clouds={"base": 3810.0, "density": 5, "thickness": 500.0,
                "preset": {"readableName": "Broken 5"}})


def _player(side="Blue", active=True):
    return types.SimpleNamespace(active=active, side=types.SimpleNamespace(name=side))


def _fleet_server(name, *, status="Running", players=None, mission=None, settings=None,
                  restart_in=None, maintenance=False, password="***", port=10308,
                  bot_port=6666, show_web_passwords=False, managed_by=None):
    # `port` is the DCS port a player joins on and belongs in `settings`, where the read model looks
    # for it; the object's own `port` is the process's BOT port in production, so the double carries
    # one too — a fixture that put the DCS port there would grade itself.
    settings = {"maxPlayers": 25, "password": password, "port": port,
                "require_pure_clients": True, "require_pure_scripts": True,
                "require_pure_models": True, "require_pure_textures": True,
                "advanced": {"allow_trial_only_clients": False}} if settings is None else settings
    return types.SimpleNamespace(
        name=name, status=_status(status), players=players or {}, current_mission=mission,
        settings=settings, maintenance=maintenance, port=Port(bot_port, PortType.UDP),
        restart_time=(datetime.now(timezone.utc) + timedelta(seconds=restart_in)
                      if restart_in is not None else None),
        node=_node(), locals={"show_web_passwords": show_web_passwords,
                              **({"managed_by": [managed_by]} if managed_by else {})})


#: the fleet the two public pages read. Root-1 is running with a mission, Root-2 is stopped with no
#: settings of its own, and Group-1 belongs to the OTHER site — it must appear on neither page.
def _root_one():
    # eight blue and three red in a slot, against the mission's 16 and 8 seats
    players = {f"b{index}": _player("Blue") for index in range(8)}
    players.update({f"r{index}": _player("Red") for index in range(3)})
    players["off"] = _player("Red", active=False)
    return _fleet_server(
        "Root-1", players=players, mission=_mission(), restart_in=6128,
        password="tusk-force-91", port=10308)


def _root_two():
    return _fleet_server("Root-2", status="Stopped", players={}, mission=None,
                         settings={"maxPlayers": 10, "port": 10309})


def _group_one():
    return _fleet_server("Group-1", port=10310, managed_by="5th Guards",
                         mission=_mission(name="Group Mission", map_name="Caucasus"))


@pytest.fixture
def fleet(bus):
    """The live roster the public site reads: three servers across two sites."""
    bus.servers = ThreadSafeDict({"Root-1": _root_one(), "Root-2": _root_two(),
                                  "Group-1": _group_one()})
    site_reads.forget_live_reads()
    yield bus
    site_reads.forget_live_reads()


def _weather_read() -> dict:
    return {"temperature": 24.6, "qnh_mmhg": 758.7, "visibility": 9400.0,
            "clouds": {"preset": "Broken 5", "base": 3810.0, "density": 5, "thickness": 500.0},
            "wind": {"ground": {"dir": 160, "speed": 4.1},
                     "low": {"dir": 175, "speed": 6.2},
                     "high": {"dir": 105, "speed": 17.5}},
            "live_visibility": True}


def _extensions_read() -> list[dict]:
    return [
        {"name": "SRS", "version": "2.1.3", "value": "51.83.14.22:5002\nExternal AWACS mode",
         "withheld": 2},
        {"name": "Tacview", "version": "1.5.2",
         "value": "51.83.14.22:42674\n- 3 s playback delay\n**Remote Ctrl [42675]**",
         "withheld": 2},
        {"name": "Slotblocking", "version": "3.2.1", "value": "16 blue and 8 red slots",
         "withheld": 0},
    ]


def prime_live(name="Root-1", *, weather=_weather_read(), extensions=None, state="ready"):
    site_reads.remember_live_read(name, {"state": state, "weather": weather,
                                         "extensions": _extensions_read() if extensions is None
                                         else extensions})


def get(app, path):
    async def run():
        async with asgi_client(app) as client:
            return await client.get(path)

    return asyncio.run(run())


# --------------------------------------------------------------------------- routes and nav

def test_the_servers_nav_entry_points_at_the_fleet_address():
    assert site_pages.NAV_PATHS["servers"] == "/fleet"
    assert site_pages.NAV_LABELS["servers"] == "Servers"
    assert "servers" in site_pages.SERVED_NAV_IDS
    site = types.SimpleNamespace(prefix="", nav=["overview", "servers", "players"])
    entries = site_pages.nav_items(site, active="servers")
    fleet_entry = next(entry for entry in entries if entry["page"] == "servers")
    assert fleet_entry["label"] == "Servers" and fleet_entry["href"] == "/fleet"
    assert fleet_entry["active"] is True


def test_a_server_name_is_one_encoded_segment_under_the_list_path():
    site = types.SimpleNamespace(prefix="")
    assert site_pages.server_url(site, "Syria '82") == "/fleet/Syria%20%2782"
    assert site_pages.server_url(site, "A/B&C?D#E") == "/fleet/A%2FB%26C%3FD%23E"
    group = types.SimpleNamespace(prefix="/group1")
    assert site_pages.server_url(group, "Root-1") == "/group1/fleet/Root-1"


# --------------------------------------------------------------------------- the list

def test_the_list_renders_one_strip_per_site_server_up_first_then_by_name(config_dir, fleet):
    page = get(build(config_dir)[0], "/fleet")
    assert page.status_code == 200, page.text[:400]
    body = page.text
    assert "Root-1" in body and "Root-2" in body
    assert "Group-1" not in body, "another site's server is on the list"
    # up first, then by name: Root-1 (running) stands above Root-2 (stopped)
    assert body.index("Root-1") < body.index("Root-2")
    # the strip's own figures
    assert "Northern Shield D3" in body and "Syria" in body
    assert "2:14:36" in body, "the runtime is not the mission clock"
    assert re.search(r">1:4\d:\d\d<", body), "the restart countdown is not drawn"
    assert ">Locked<" in body
    assert ">11</b>" in body, "the players-in-a-slot figure is not drawn"


def test_the_strips_slots_are_the_live_sides_and_their_totals(config_dir, fleet):
    body = get(build(config_dir)[0], "/fleet").text
    assert "8 / 16" in body and "3 / 8" in body, "the side slot figures are not the mission's"
    assert 'style="width:50.0%"' in body, "the blue side's occupancy bar is not scaled"
    assert 'style="width:37.5%"' in body, "the red side's occupancy bar is not scaled"


def test_the_list_answers_a_site_with_no_server_with_its_own_sentence(config_dir, fleet):
    fleet.servers = ThreadSafeDict({})
    page = get(build(config_dir)[0], "/fleet")
    assert page.status_code == 200
    assert "This site has no server of its own yet." in page.text


# --------------------------------------------------------------------------- one server

def test_the_detail_page_renders_the_embed_inventory(config_dir, fleet):
    prime_live()
    page = get(build(config_dir)[0], "/fleet/Root-1")
    assert page.status_code == 200, page.text[:600]
    body = page.text
    assert "51.83.14.22:10308" in body, "the address is not the node's public ip:port"
    assert ":6666" not in body, "the process's own bot port was printed as part of the address"
    assert ">Locked<" in body
    assert "A password is required to join" in body, "the lock chip carries no sentence"
    assert "8 of 16" in body and "3 of 8" in body, "the per-side slot usage is missing"
    assert "14 Jun 1987" in body and "11:56:54" in body, "the mission clock is missing"
    assert "2:14:36" in body
    assert re.search(r">1:4\d:\d\d<", body)
    for label in ("No trial clients", "Pure clients", "Pure scripts", "Pure models",
                  "Pure textures"):
        assert label in body, label
    assert "Pure scripts" in body
    assert "2.9.8.1108" in body, "the DCS version is missing"
    assert "3.6.2" in body, "the bot version is missing"
    assert "Last updated" in body


def test_the_locked_sentence_carries_no_password_value(config_dir, fleet):
    prime_live()
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "tusk-force-91" not in body, "the page published the server password"


def test_the_detail_page_of_another_site_s_server_is_not_found(config_dir, fleet):
    page = get(build(config_dir)[0], "/fleet/Group-1")
    assert page.status_code == 404
    assert "Group Mission" not in page.text and "Caucasus" not in page.text
    assert "was not found" in page.text


def test_an_unknown_name_answers_the_pages_own_not_found_state(config_dir, fleet):
    page = get(build(config_dir)[0], "/fleet/No Such Server")
    assert page.status_code == 404
    assert "/fleet/No%20Such%20Server" in page.text
    assert "Northern Shield D3" not in page.text


def test_a_group_site_serves_its_own_server_and_only_its_own(config_dir, fleet):
    app, _ = build(config_dir)
    own = get(app, "/group1/fleet/Group-1")
    foreign = get(app, "/group1/fleet/Root-1")
    assert own.status_code == 200 and "Group Mission" in own.text
    assert foreign.status_code == 404 and "Northern Shield D3" not in foreign.text


# --------------------------------------------------------------------------- the live blocks

def test_the_weather_block_renders_the_cached_read(config_dir, fleet):
    prime_live()
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "cached live read" in body and "300" in body
    assert "24.6 °C" in body
    assert "1011 hPa" in body and "29.87 inHg" in body
    assert "Broken 5" in body and "12,500 ft" in body
    assert "9,400 m" in body and "5.84 SM" in body
    assert "340° / 08 kts" in body, "the ground wind is not the embed's own offset and rounding"


@pytest.mark.parametrize("state", ["down", "failed"])
def test_a_live_block_that_cannot_be_read_states_that_in_one_sentence(config_dir, fleet, state):
    site_reads.remember_live_read("Root-1", {"state": state, "weather": None, "extensions": []})
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "live read" in body.lower() or "not running" in body.lower()
    assert "0.0 °C" not in body and "0 hPa" not in body
    assert "24.6 °C" not in body


def test_the_extension_tiles_are_plain_lines_and_never_a_credential(config_dir, fleet):
    prime_live()
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "SRS" in body and "2.1.3" in body
    assert "51.83.14.22:5002" in body
    assert "**" not in body, "a Discord markdown mark reached the page"
    assert "Remote Ctrl [42675]" in body and "- 3 s playback delay" not in body
    for banned in ("Pass:", "Password:", "Admin password:", "GameMaster:", "tusk-force-91"):
        assert banned not in body, f"the tile printed {banned}"
    assert "2 credential line(s) withheld" in body


def test_a_pending_live_read_states_that_it_is_on_its_way(config_dir, fleet):
    site_reads.remember_live_read("Root-1", {"state": "pending"})
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "cached live read" in body


# --------------------------------------------------------------------------- a silent fog query

#: The live read's hang detector for the cases below, in seconds. A read that awaits a silent RPC
#: unbounded never returns, so the case is abandoned here and fails as a timeout. This is NOT a
#: latency budget: it sits far above both RPC bounds (:data:`site_reads.FOG_TIMEOUT`,
#: :data:`site_reads.EXTENSIONS_TIMEOUT`) and far above the scheduling jitter of a full parallel
#: run, so it can only ever catch a call that does not come back at all. What the old unbounded
#: call is caught by is BEHAVIOUR — it never returned a READY payload, so each case's own payload
#: assertions fail it before this ceiling is reached.
HANG_CEILING = 30.0


def _read_live_within_ceiling(server) -> dict:
    """The server's real live read; the case fails if it does not return inside :data:`HANG_CEILING`."""
    async def read():
        return await asyncio.wait_for(site_reads._read_live(server), HANG_CEILING)

    try:
        return asyncio.run(read())
    except TimeoutError:
        pytest.fail(f"the live read did not return within {HANG_CEILING:.0f}s — an RPC is awaited "
                    f"without its bound")


def _stub_rpcs(server, *, fog, extensions) -> None:
    """Replace a server's two live RPCs: *fog* answers ``getFog``, *extensions* the public render."""
    server.send_to_dcs_sync = fog
    server.render_extensions = extensions


def _tile_names(payload: dict) -> list[str]:
    """The extension tile names a live payload carries, in order."""
    return [entry["name"] for entry in payload["extensions"]]


def test_a_fog_query_that_never_returns_costs_only_the_live_visibility(config_dir, fleet):
    """``getFog`` hangs: the read answers READY, on the mission's own figures, and the page draws it."""
    server = fleet.servers["Root-1"]
    gate = asyncio.Event()

    async def fog(command):
        await gate.wait()

    async def extensions(public=False):
        return _extensions_read()

    _stub_rpcs(server, fog=fog, extensions=extensions)
    payload = _read_live_within_ceiling(server)
    assert payload["state"] == site_reads.LIVE_READY
    assert payload["weather"]["visibility"] == 9400.0, "the mission's own visibility is not used"
    assert payload["weather"]["live_visibility"] is False
    assert payload["extensions_state"] == site_reads.BLOCK_READY
    assert _tile_names(payload) == ["SRS", "Tacview", "Slotblocking"]
    site_reads.remember_live_read("Root-1", payload)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "24.6 °C" in body and "9,400 m" in body, "the weather cells are not drawn"
    assert "SRS" in body and "51.83.14.22:5002" in body, "the extension tiles are not drawn"
    assert site_servers.LIVE_PENDING not in body


def test_a_fog_query_that_raises_changes_nothing_else(config_dir, fleet):
    """``getFog`` raises: the same payload and the same page as a hung one."""
    server = fleet.servers["Root-1"]

    async def fog(command):
        raise RuntimeError("DCS did not answer")

    async def extensions(public=False):
        return _extensions_read()

    _stub_rpcs(server, fog=fog, extensions=extensions)
    payload = asyncio.run(site_reads._read_live(server))
    assert payload["state"] == site_reads.LIVE_READY
    assert payload["weather"]["visibility"] == 9400.0
    assert payload["weather"]["live_visibility"] is False
    assert payload["extensions_state"] == site_reads.BLOCK_READY
    assert _tile_names(payload) == ["SRS", "Tacview", "Slotblocking"]
    site_reads.remember_live_read("Root-1", payload)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "24.6 °C" in body and "9,400 m" in body
    assert "SRS" in body and "51.83.14.22:5002" in body
    assert site_servers.LIVE_PENDING not in body


def test_an_extensions_render_that_never_returns_states_it_and_draws_the_weather(config_dir, fleet):
    """``render_extensions`` hangs: the read returns, the weather is READY, extensions say failed."""
    server = fleet.servers["Root-1"]

    async def fog(command):
        return {"visibility": 1200.0}

    async def extensions(public=False):
        await asyncio.Event().wait()

    _stub_rpcs(server, fog=fog, extensions=extensions)
    payload = _read_live_within_ceiling(server)
    assert payload["state"] == site_reads.LIVE_READY
    assert payload["weather_state"] == site_reads.BLOCK_READY
    assert payload["extensions_state"] == site_reads.BLOCK_FAILED
    assert payload["extensions"] == []
    assert payload["weather"]["visibility"] == 1200.0, "the fog answer is not the live visibility"
    assert payload["weather"]["live_visibility"] is True
    site_reads.remember_live_read("Root-1", payload)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "24.6 °C" in body and "1,200 m" in body, "the weather cells are not drawn"
    assert site_servers.LIVE_FAILED in body, "the extensions block states no failure"
    assert site_servers.LIVE_PENDING not in body
    assert site_servers.NO_EXTENSIONS not in body


def test_a_block_that_did_not_answer_logs_one_warning_without_its_payload(fleet, caplog):
    """Each hung block is ONE warning line: the server, the block and the failure type.

    The two blocks fail concurrently, so their lines arrive in no fixed order: the lines are read as
    a set of one per block, never by position.
    """
    server = fleet.servers["Root-1"]

    async def fog(command):
        await asyncio.Event().wait()

    async def extensions(public=False):
        raise RuntimeError("the render is broken")

    _stub_rpcs(server, fog=fog, extensions=extensions)
    with caplog.at_level(logging.WARNING, logger=site_reads.log.name):
        _read_live_within_ceiling(server)
    lines = [record.getMessage() for record in caplog.records
             if record.name == site_reads.log.name]
    assert len(lines) == 2, lines
    by_block = {block: [line for line in lines if f" {block} " in line]
                for block in ("weather", "extensions")}
    assert {block: len(found) for block, found in by_block.items()} == {"weather": 1,
                                                                       "extensions": 1}, lines
    (weather,) = by_block["weather"]
    (ext_line,) = by_block["extensions"]
    assert "Root-1" in weather and "TimeoutError" in weather
    assert "Root-1" in ext_line and "RuntimeError" in ext_line
    assert not any("24.6" in line or "9400" in line for line in lines), "a payload reached the log"


def test_a_read_that_raises_outright_is_cached_as_failed(fleet, monkeypatch):
    """The failure is CACHED: the second view answers ``failed``, never ``pending`` again."""
    server = fleet.servers["Root-1"]

    async def boom(*args, **kwargs):
        raise RuntimeError("the read blew up")

    monkeypatch.setattr(site_reads, "_read_live", boom)

    async def run():
        first = site_reads.live_read(server)
        await site_reads._live_refreshing[server.name]
        return first, dict(site_reads._live_cache[server.name][1]), site_reads.live_read(server)

    first, cached, second = asyncio.run(run())
    assert first["state"] == site_reads.LIVE_PENDING
    assert cached["state"] == site_reads.LIVE_FAILED
    assert second["state"] == site_reads.LIVE_FAILED


def test_the_first_view_is_pending_and_the_second_is_the_cached_read(fleet):
    """The pending sentence belongs to the very first view only."""
    server = fleet.servers["Root-1"]

    async def fog(command):
        return {"visibility": 9400.0}

    async def extensions(public=False):
        return _extensions_read()

    _stub_rpcs(server, fog=fog, extensions=extensions)

    async def run():
        first = site_reads.live_read(server)
        await site_reads._live_refreshing[server.name]
        return first, site_reads.live_read(server)

    first, second = asyncio.run(run())
    assert first["state"] == site_reads.LIVE_PENDING
    assert second["state"] == site_reads.LIVE_READY
    assert second["weather"]["visibility"] == 9400.0
    assert second["extensions_state"] == site_reads.BLOCK_READY


def test_the_headers_yes_no_figures_come_from_the_fleet(config_dir, fleet):
    body = get(build(config_dir)[0], "/fleet").text
    assert "1 of 2 up" in body
    assert "11 pilots in the air" in body


def test_the_maintenance_flag_is_never_published(config_dir, fleet):
    """A server's maintenance flag is the operator's business, not the public's.

    The list drew a "Maintenance" chip from it and drew it on servers that were not flagged; the
    public site carries the flag nowhere at all — not in the page, not in the polled JSON, not in
    the read's own row.
    """
    fleet.servers["Root-1"].maintenance = True
    app = build(config_dir)[0]
    body = get(app, "/fleet").text
    assert "Maintenance" not in body, "the fleet page still draws a maintenance chip"
    assert "chip maint" not in body
    answer = get(app, "/fleet.json").json()
    assert all("maintenance" not in row for row in answer["rows"]), answer["rows"]
    row = next(row for row in site_reads.fleet_live(types.SimpleNamespace(
        servers=frozenset({"Root-1"}), prefix="")) if row["name"] == "Root-1")
    assert "maintenance" not in row, "the read still hands the flag to the page"


# ------------------------------------------------- a cached read the roster contradicts
#
# THE OWNER'S REPORT: the per-server page said "the server is not running, so there is no live
# report to read" about a server that was up, and named neither weather nor extensions. The read
# that said `down` was taken while the server was off and stood for its whole 300 s window; the
# roster had the server running the whole time.

def test_a_snapshot_taken_while_the_server_was_off_is_never_served_once_it_runs(fleet):
    server = fleet.servers["Root-1"]
    site_reads.remember_live_read("Root-1", site_reads._payload(site_reads.LIVE_DOWN))
    payload = site_reads.live_read(server)
    assert payload["state"] != site_reads.LIVE_DOWN, (
        "a running server was reported as not running from a stale snapshot")
    assert payload["state"] == site_reads.LIVE_PENDING


def test_a_ready_snapshot_is_dropped_when_the_server_stops(fleet):
    """The other direction: figures read while it ran outlive the server without this."""
    server = fleet.servers["Root-2"]
    prime_live("Root-2")
    payload = site_reads.live_read(server)
    assert payload["state"] == site_reads.LIVE_PENDING


def test_a_down_snapshot_stands_for_the_short_window_only(fleet, monkeypatch):
    server = fleet.servers["Root-2"]
    site_reads.remember_live_read("Root-2", site_reads._payload(site_reads.LIVE_DOWN))
    payload = site_reads.live_read(server)
    assert payload["state"] == site_reads.LIVE_DOWN, "a stopped server is not reported as down"
    assert site_reads.LIVE_STALE_TTL < site_reads.LIVE_READ_TTL


def test_the_detail_page_never_calls_a_running_server_stopped(config_dir, fleet):
    """The page's own model, on a `down` snapshot and a running roster row."""
    server = fleet.servers["Root-1"]
    row = site_reads._server_row("Root-1", server, {})
    model = site_servers.detail_view(
        {"found": True, "server": row, "limits": [], "mission_clock": {},
         "dcs_version": "", "bot_version": "", "updated": ""},
        types.SimpleNamespace(prefix=""), site_reads._payload(site_reads.LIVE_DOWN))
    assert model["weather_sentence"] != site_servers.LIVE_NOT_RUNNING
    assert model["live_state"] != "down"


def test_the_detail_page_still_states_it_plainly_for_a_stopped_server(config_dir, fleet):
    server = fleet.servers["Root-2"]
    row = site_reads._server_row("Root-2", server, {})
    model = site_servers.detail_view(
        {"found": True, "server": row, "limits": [], "mission_clock": {},
         "dcs_version": "", "bot_version": "", "updated": ""},
        types.SimpleNamespace(prefix=""), site_reads._payload(site_reads.LIVE_READY))
    assert model["weather_sentence"] == site_servers.LIVE_NOT_RUNNING


# ------------------------------------------- the weather the mission object does not carry
#
# THE OWNER'S REPORT: the page said "The mission reports no weather." while the status embed showed
# the full block. The embed is drawn where the mission data arrives; the page can be served from a
# process holding the mission's name, map and slots with an empty weather block. The game server is
# the source of record, so the read asks it the same `getWeatherInfo` the bot's own listener uses.

#: the owner's own figures, from his embed, back in the units DCS reports.
WEATHER_INFO = {
    "command": "getWeatherInfo",
    "weather": {"season": {"temperature": 34.0},
                "qnh": 772.99,
                "visibility": {"distance": 60000.0},
                "wind": {"atGround": {"dir": 250.0, "speed": 8 / 1.943844},
                         "at2000": {"dir": 245.0, "speed": 16 / 1.943844},
                         "at8000": {"dir": 240.0, "speed": 25 / 1.943844}}},
    "clouds": {"base": 2500.0, "density": 3, "thickness": 200.0,
               "preset": {"readableName": "Preset/Few Scattered Clouds"}},
}


def _server_without_weather():
    """A running server whose mission object carries no weather block in this process."""
    server = _fleet_server("Root-1", mission=_mission())
    server.current_mission.weather = {}
    server.current_mission.clouds = {}

    async def dcs(message, timeout=None):
        return WEATHER_INFO if message.get("command") == "getWeatherInfo" else {
            "visibility": 60000.0}

    async def extensions(public=False):
        return _extensions_read()

    server.send_to_dcs_sync = dcs
    server.render_extensions = extensions
    return server


def test_the_weather_is_read_from_the_game_server_when_the_mission_has_none():
    server = _server_without_weather()
    assert site_reads._weather_payload(server, None) is None, (
        "the fixture's mission carries weather, so this pin would not exercise the fallback")
    payload = _read_live_within_ceiling(server)
    assert payload["weather_state"] == site_reads.BLOCK_READY, payload["weather_state"]
    assert payload["weather"]["temperature"] == 34.0
    assert payload["weather"]["clouds"]["preset"] == "Few Scattered Clouds"
    assert payload["extensions_state"] == site_reads.BLOCK_READY


def test_the_page_draws_the_owner_s_embed_figures_from_that_read():
    server = _server_without_weather()
    payload = _read_live_within_ceiling(server)
    row = site_reads._server_row("Root-1", server, {})
    model = site_servers.detail_view(
        {"found": True, "server": row, "limits": [], "mission_clock": {},
         "dcs_version": "", "bot_version": "", "updated": ""},
        types.SimpleNamespace(prefix=""), payload)
    cells = {cell["key"]: cell for cell in model["weather"]["cells"]}
    wind = {line["label"]: line["value"] for line in model["weather"]["wind"]}
    assert model["weather_sentence"] != site_servers.WEATHER_EMPTY
    assert cells["temperature"]["value"] == "34.0 °C"
    assert cells["qnh"]["value"] == "30.43 inHg" and cells["qnh"]["note"] == "1030 hPa"
    assert cells["visibility"]["value"] == "10 km" and cells["visibility"]["note"] == "6 SM (+)"
    assert cells["clouds"]["value"] == "Few Scattered Clouds"
    assert "8,202 ft" in cells["clouds"]["note"]
    assert wind == {"Ground": "70° / 08 kts", "6,600 ft": "65° / 16 kts",
                    "26,000 ft": "60° / 25 kts"}


def test_a_snapshot_cached_without_weather_is_filled_on_the_next_view(fleet):
    """A read cached while the mission was still loading must not hide the weather for 300 s."""
    server = fleet.servers["Root-1"]
    site_reads.remember_live_read("Root-1", site_reads._payload(site_reads.LIVE_READY))
    payload = site_reads.live_read(server)
    assert payload["weather"], "an empty weather block stood for the whole cache window"
    assert payload["weather"]["temperature"] == 24.6
    assert payload["weather_state"] == site_reads.BLOCK_READY


# ------------------------------------------------------------------- the polled live cells
def test_the_live_cells_answer_the_figures_the_page_renders(config_dir, fleet):
    site = types.SimpleNamespace(servers=frozenset({"Root-1", "Root-2"}), prefix="")
    answer = site_servers.live_cells(site_reads.fleet_live(site, fleet.servers), site)
    assert answer["up"] == "1 of 2 up" and answer["flying"] == "11 pilots in the air"
    rows = {row["name"]: row for row in answer["rows"]}
    assert rows["Root-1"]["state"] == "Running" and rows["Root-1"]["players"] == "11"
    assert rows["Root-1"]["blue_text"] == "8 / 16" and rows["Root-1"]["slots"] == "sides"
    assert rows["Root-2"]["state"] == "Stopped" and rows["Root-2"]["slots"] == "none"
    assert rows["Root-2"]["mission"] == site_servers.NO_MISSION


def test_the_fleet_live_route_answers_the_poll_it_exists_for(config_dir, fleet):
    reply = get(build(config_dir)[0], site_pages.FLEET_LIVE_PATH)
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    answer = reply.json()
    assert answer["up"] == "1 of 2 up" and answer["flying"] == "11 pilots in the air"
    assert {row["name"] for row in answer["rows"]} == {"Root-1", "Root-2"}
    assert site_pages.FLEET_LIVE_PATH not in site_pages.NAV_PATHS.values(), (
        "the JSON is an address of its own, never a nav entry")


def test_the_fleet_page_hands_the_poll_its_own_address(config_dir, fleet):
    body = get(build(config_dir)[0], "/fleet").text
    assert 'data-live-board="/fleet.json"' in body
    for hook in ('data-live="state"', 'data-live="players"', 'data-live="runtime"',
                 'data-live="restart"', 'data-live="blue-text"'):
        assert hook in body, f"the page carries no {hook} hook for the poll to write"


def test_the_server_page_carries_its_own_poll_and_the_state_it_was_drawn_from(config_dir, fleet):
    """THE OWNER'S REPORT: the per-server page was only live after a manual reload."""
    from services.webservice import site_reads as reads

    reads.remember_live_read("Root-1", reads._payload(reads.LIVE_PENDING))
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert 'data-live-server="Root-1"' in body
    assert 'data-live-state="pending"' in body
    assert 'data-live-board="/fleet.json"' in body


def test_the_single_server_poll_answers_that_server_and_its_live_state(config_dir, fleet):
    from services.webservice import site_reads as reads

    reads.forget_live_reads()
    reply = get(build(config_dir)[0], f"{site_pages.FLEET_LIVE_PATH}?server=Root-1")
    assert reply.status_code == 200
    answer = reply.json()
    assert answer["found"] is True and answer["row"]["name"] == "Root-1"
    assert answer["row"]["state"] == "Running"
    assert answer["live_state"] in ("pending", "ready"), answer["live_state"]
    missing = get(build(config_dir)[0], f"{site_pages.FLEET_LIVE_PATH}?server=Group-1")
    assert missing.status_code == 404, "another site's server was answered as this site's"


def test_the_fleet_view_warms_the_live_read_of_every_server_it_lists(fleet):
    """A visitor clicking through the fleet must not be the one who pays each server's read."""
    from services.webservice import site_reads as reads

    site = types.SimpleNamespace(servers=frozenset({"Root-1", "Root-2"}), prefix="")
    registry = {name: fleet.servers[name] for name in site.servers}
    reads.forget_live_reads()

    async def run():
        scheduled = reads.prefetch_live(registry)
        for task in list(reads._live_refreshing.values()):
            await task
        return scheduled

    assert asyncio.run(run()) == 1, "a stopped server has nothing to read, a running one has"
    assert set(reads._live_cache) == {"Root-1"}


#: the shipped poll and the harness that drives it: the FILE the browser runs, never a copy.
SITE_LIVE_JS = REPO_ROOT / "services" / "webservice" / "static" / "site-live.js"
SITE_LIVE_HARNESS = REPO_ROOT / "tests" / "site_live_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def poll() -> dict:
    """The shipped ``site-live.js``'s own answers, driven by the harness over the REAL file."""
    assert SITE_LIVE_JS.is_file() and SITE_LIVE_HARNESS.is_file()
    done = subprocess.run(["node", str(SITE_LIVE_HARNESS), str(SITE_LIVE_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@requires_node
def test_the_poll_writes_every_cell_its_answer_carries(poll):
    """The answer's values reach the cells the page drew, and the row's own state flips."""
    painted = poll["painted"]
    assert poll["interval"] == 10_000, "the fleet is polled on a ten-second beat"
    assert poll["requested"] == "/fleet.json"
    assert painted["up"] == "2 of 2 up" and painted["flying"] == "3 pilots in the air"
    live, idle = painted["rows"]
    assert live["state"]["text"] == "Paused" and live["dot"]["className"] == "dot st-paused"
    assert live["players"]["text"] == "3" and live["mission"]["text"] == "Northern Shield D3"
    assert live["mission"]["className"] == "m" and live["map"]["text"] == "Syria"
    assert live["runtime"]["text"] == "2:14:36" and live["restart"]["className"] == "v hot"
    assert live["blueBar"]["width"] == "12.5%" and live["redText"]["text"] == "1 / 8"
    assert idle["state"]["text"] == "Running" and idle["mission"]["className"] == "m quiet"
    assert idle["none"]["display"] == "" and idle["sides"]["display"] == "none"


@requires_node
def test_a_refused_or_hidden_poll_leaves_the_page_alone(poll):
    unchanged = poll["afterRefusal"]
    assert unchanged["up"] == "1 of 2 up", "a refused poll rewrote the header"
    assert unchanged["rows"][0]["state"]["text"] == "Running"
    assert poll["hiddenRequests"] == 0, "a background tab kept polling"


@requires_node
def test_the_poll_exists_on_the_fleet_page_only(poll):
    assert poll["otherPagesPoll"] == 0, "every site page loads the script; only the fleet polls"


@requires_node
def test_a_page_drawn_before_the_live_read_lands_reloads_once(poll):
    """THE OWNER'S REPORT: after a restart the page showed the pending sentence until he reloaded.

    The per-server page polls its own strip; when it was drawn from `pending` and the cached live
    read has landed, the browser takes the page once — and a page drawn from a settled answer, or a
    second poll, never reloads again.
    """
    assert poll["singleRequested"] == "/fleet.json?server=Root-1"
    first = poll["single"]["afterFirst"]
    assert first["state"] == "Paused", "the strip was not repainted from the poll"
    assert first["navigations"] == ["/fleet/Root-1"]
    assert poll["single"]["navigations"] == ["/fleet/Root-1"], "a settled page reloaded twice"
    assert poll["singleSettled"] == [], "a page drawn from a settled answer reloaded"


# --------------------------------------------------------------------------- the read layer

def test_the_fleet_read_binds_the_site_servers_into_its_statement(fleet):
    site = types.SimpleNamespace(servers=frozenset({"Root-1"}))
    pool = StubPool()
    rows = asyncio.run(site_reads.server_list(site, pool, fleet.servers))
    assert [row["name"] for row in rows] == ["Root-1"]
    assert pool.calls, "the fleet read ran no statement"
    for statement, params in pool.calls:
        assert site_reads.SERVER_FILTER in statement
        assert params["servers"] == ["Root-1"]


def test_the_detail_read_answers_not_found_for_another_site_s_server(fleet):
    site = types.SimpleNamespace(servers=frozenset({"Root-1"}))
    pool = StubPool()
    answer = asyncio.run(site_reads.server_detail(site, pool, fleet.servers, "Group-1"))
    assert answer["found"] is False
    found = asyncio.run(site_reads.server_detail(site, pool, fleet.servers, "Root-1"))
    assert found["found"] is True
    assert found["address"] == "51.83.14.22:10308"


# ---------------------------------------------------------- the list lives on the Servers page

def test_the_servers_page_still_draws_every_server_and_its_strips(config_dir, fleet):
    """The control pin: the overview's retired band is not a loss — /fleet carries the list whole.

    Green before and after the band's removal: every server of the site gets one strip, each
    strip's name is its own page's address, and another site's server is on neither page.
    """
    app, _ = build(config_dir)
    root = get(app, "/fleet").text
    rows = root.count('<article class="srow')
    assert rows == 2, f"/fleet draws {rows} row(s) for two servers"
    assert "Root-1" in root and "Root-2" in root
    assert "Group-1" not in root, "another site's server is on this fleet page"
    site = types.SimpleNamespace(prefix="")
    assert f'<a href="{site_pages.server_url(site, "Root-1")}">' in root
    assert f'<a href="{site_pages.server_url(site, "Root-2")}">' in root

    group = get(app, "/group1/fleet").text
    assert "Group-1" in group and "Root-1" not in group


def test_the_overview_no_longer_holds_the_band(config_dir, fleet):
    """The removal itself: no band, no table, no fleet link — and the sections still stand."""
    body = get(build(config_dir)[0], "/").text
    assert 'id="servers"' not in body
    assert "The servers" not in body
    assert "Server board" not in body
    assert "All servers</a>" not in body, "the band's fleet link outlived the band"
    for header in ("<th>State</th>", "<th>Server</th>", '<th class="r">Flying</th>',
                   '<th class="r hide-sm">Sorties</th>'):
        assert header not in body, f"the '{header}' column survives"
    assert "The season so far" in body and "The standings" in body
    assert "Live" in body, "the live band is not a section and stays"


def test_the_leaderboard_still_answers_its_own_server_filter(config_dir, fleet):
    """The control pin: the leaderboard's ``?server=`` is untouched by the band's removal."""
    page = get(build(config_dir)[0], f"/rankings?{site_leaderboard.SERVER_PARAM}=Root-1")
    assert page.status_code == 200
    assert "Root-1" in page.text


# --------------------------------------------------------------------------- one sentence per state

def test_every_live_sentence_is_a_marked_msgid():
    for sentence in (site_servers.LIVE_NOT_RUNNING, site_servers.LIVE_FAILED,
                     site_servers.LIVE_PENDING, site_servers.WEATHER_EMPTY):
        assert isinstance(sentence, str) and sentence


# --------------------------------------------------------------------------- the clouds cell

def _clouds(**clouds) -> dict:
    """The cached weather read with its clouds block replaced."""
    return {**_weather_read(), "clouds": clouds}


def test_a_cloud_preset_names_the_preset_and_never_its_density(config_dir, fleet):
    """A preset prints the preset's readable name and the cloudbase — the density is not appended."""
    prime_live(weather=_clouds(preset="Broken 5", base=3810.0, density=5, thickness=500.0))
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "Broken 5 5" not in body, "the clouds cell printed its figure twice"
    assert ">Broken 5<small>cloudbase 12,500 ft" in body


def test_a_sky_with_no_preset_and_no_density_reads_clear(config_dir, fleet):
    """No preset with ``density == 0`` is the embed's own "Clear", and it carries no figure."""
    prime_live(weather=_clouds(preset="", base=0.0, density=0, thickness=0.0))
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert '<span class="v">Clear</span>' in body, "the clouds cell does not read Clear"
    assert "Clear<small>" not in body, "the Clear cell carries a figure"


def test_a_dynamic_sky_puts_its_figures_in_the_note(config_dir, fleet):
    """A dynamic sky prints "Dynamic"; base, density and thickness belong in the note under it."""
    prime_live(weather=_clouds(preset="", base=2000.0, density=3, thickness=300.0))
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert ">Dynamic<small>Base: 6,562 ft · Density: 3/10 · Thickness: 984 ft</small>" in body
    assert "Dynamic 3" not in body


# --------------------------------------------------------------------------- the weekly schedule

class StubScheduler:
    """The Scheduler cog as the live read reaches it: one config per server and a call count.

    ``error`` makes ``get_config`` raise, which is the read that failed; ``install`` swaps the stub
    into the registry, so a test can state the plugin's absence by installing ``None``.
    """

    def __init__(self, config=None, error=None):
        self.config = dict(config or {})
        self.error = error
        self.calls = 0

    def get_config(self, server=None, *, use_cache=True):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.config)


@pytest.fixture
def scheduler(monkeypatch):
    """A bot whose only cog is the Scheduler stub — what ``site_reads`` asks for the schedule."""
    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    cogs: dict = {}
    monkeypatch.setitem(ServiceRegistry._singletons, BotService,
                        types.SimpleNamespace(bot=types.SimpleNamespace(cogs=cogs)))

    def install(stub):
        cogs.clear()
        if stub is not None:
            cogs["Scheduler"] = stub
        return stub

    install(None)
    return types.SimpleNamespace(install=install)


async def _live(server) -> dict:
    """The server's real live read, its two RPCs stubbed: the schedule arrives with the weather."""
    async def send_to_dcs_sync(command):
        return {"visibility": 9400.0}

    async def render_extensions(public=False):
        return _extensions_read()

    server.send_to_dcs_sync = send_to_dcs_sync
    server.render_extensions = render_extensions
    return await site_reads._read_live(server)


def prime_plugin(fleet, stub, name="Root-1") -> dict:
    """Read *name* through the real live read and file the answer as its cached payload."""
    payload = asyncio.run(_live(fleet.servers[name]))
    site_reads.remember_live_read(name, payload)
    return payload


def test_the_weekly_schedule_draws_the_week_the_plugin_states(config_dir, fleet, scheduler):
    """Every period row, the seven day cells in their three states, and the legend under them."""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP", "22-06": "NNNYYNN"}}))
    prime_plugin(fleet, stub)
    page = get(build(config_dir)[0], "/fleet/Root-1")
    assert page.status_code == 200, page.text[:600]
    body = page.text
    assert "The weekly schedule" in body and "When it runs" in body
    assert "00:00 – 24:00" in body and "22:00 – 06:00" in body, "the period rows are not drawn"
    assert body.count('class="sched"') == 2, "one week grid per period is not drawn"
    assert body.count('class="day on"') == 7
    assert body.count('class="day idle"') == 2
    assert body.count('class="day off"') == 5
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert f'<span class="d">{day}</span>' in body, f"the {day} cell is missing"
    assert 'title="shuts down with no players"' in body, "a cell carries no state word"
    assert ('aria-label="The week: Mon runs, Tue runs, Wed runs, Thu runs, Fri runs, '
            'Sat shuts down with no players, Sun shuts down with no players."') in body
    assert '<i class="on"></i>runs' in body
    assert '<i class="idle"></i>shuts down with no players' in body
    assert '<i class="off"></i>off' in body


def test_the_page_render_never_asks_the_scheduler(config_dir, fleet, scheduler):
    """The plugin is asked INSIDE the cached live read: a page view leaves its counter unchanged."""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"}}))
    server = fleet.servers["Root-1"]
    payload = asyncio.run(_live(server))
    assert payload["schedule"]["state"] == site_reads.SCHEDULE_READY
    assert payload["schedule"]["rows"] == [{"period": "00-24", "days": "YYYYYPP"}]
    assert stub.calls == 1
    site_reads.remember_live_read("Root-1", payload)
    app = build(config_dir)[0]
    get(app, "/fleet/Root-1")
    get(app, "/fleet/Root-1")
    assert stub.calls == 1, "the render path called the scheduler plugin"


@pytest.mark.parametrize("config", [None, {}, {"timezone": "Europe/Berlin"},
                                    {"schedule": {}}, {"schedule": {"00-24": "YYYYYYY"}}])
def test_the_schedule_block_is_hidden_whole_without_a_schedule(config_dir, fleet, scheduler,
                                                               config):
    """No plugin, no ``schedule``, or the trivial always-on week: heading, grid and legend all go."""
    stub = scheduler.install(None if config is None else StubScheduler(config))
    prime_plugin(fleet, stub)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "The weekly schedule" not in body, "the block's heading is drawn without a schedule"
    assert "When it runs" not in body
    assert 'class="sched"' not in body and "sched-legend" not in body


@pytest.mark.parametrize("config", [{"error": RuntimeError("unreadable")},
                                    {"schedule": {"00-24": "YYY"}},
                                    {"schedule": {"00-24": "YYYYYYX"}}])
def test_a_failed_schedule_read_states_it_in_one_sentence(config_dir, fleet, scheduler, config):
    """An unreadable config is one sentence and no grid — never an empty week, never zeros."""
    if "error" in config:
        stub = scheduler.install(StubScheduler(error=config["error"]))
    else:
        stub = scheduler.install(StubScheduler(config))
    prime_plugin(fleet, stub)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "The weekly schedule could not be read." in body
    assert 'class="sched"' not in body, "a failed read drew a grid"
    assert '<i class="on"></i>' not in body, "a failed read drew the legend"


def test_the_schedule_names_a_timezone_only_when_the_config_does(config_dir, fleet, scheduler):
    """The block's sub line is the config's own timezone; a config that states none prints none."""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"},
                                            "timezone": "Europe/Berlin"}))
    prime_plugin(fleet, stub)
    named = get(build(config_dir)[0], "/fleet/Root-1").text
    assert '<div class="sub">Europe/Berlin</div>' in named

    site_reads.forget_live_reads()
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"}}))
    prime_plugin(fleet, stub)
    unnamed = get(build(config_dir)[0], "/fleet/Root-1").text
    assert "Europe/Berlin" not in unnamed
    assert "The weekly schedule" in unnamed, "the block vanished with its timezone"


def test_the_schedule_sits_after_client_limits_and_before_extensions(config_dir, fleet,
                                                                     scheduler):
    """The page's block order is the mockup's: … client limits, the schedule, extensions …"""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"}}))
    prime_plugin(fleet, stub)
    body = get(build(config_dir)[0], "/fleet/Root-1").text
    limits = body.index("What the server enforces")
    schedule = body.index("The weekly schedule")
    extensions = body.index("What is loaded")
    assert limits < schedule < extensions, "the schedule is not between limits and extensions"


def test_a_stopped_server_still_states_its_week(config_dir, fleet, scheduler):
    """The schedule is config, not a live figure: a stopped server keeps its block."""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"}}))
    prime_plugin(fleet, stub, "Root-2")
    body = get(build(config_dir)[0], "/fleet/Root-2").text
    assert "The weekly schedule" in body and "00:00 – 24:00" in body
    assert "not running" in body.lower(), "the live blocks no longer state the server is down"


# --------------------------------------------------------------------------- the page is translated

def test_the_fleet_page_renders_in_german(config_dir, fleet):
    """The compiled ``webui`` catalog reaches both pages: the list's chrome and the strip's words."""
    app = build(config_dir)[0]

    async def run():
        async with asgi_client(app) as client:
            client.cookies.set(i18n.COOKIE_NAME, "de")
            return await client.get("/fleet"), await client.get("/fleet/Root-1")

    listing, detail = asyncio.run(run())
    assert listing.status_code == detail.status_code == 200
    assert "Die Flotte" in listing.text, "the page's label is not German"
    assert "Gesperrt" in listing.text, "the lock chip is not German"
    assert "Laufzeit" in listing.text, "the runtime label is not German"
    assert "The fleet" not in listing.text
    # the detail page reads the same catalog: its rails, its chips and its live-block label
    assert "Verbindung" in detail.text and "Wetter" in detail.text
    assert "Zuletzt aktualisiert" in detail.text
    assert "zwischengespeicherte Live-Abfrage" in detail.text
    assert "Connection" not in detail.text


def test_the_weekly_schedule_renders_in_german(config_dir, fleet, scheduler):
    """The compiled catalog carries the block's chrome and its three state words."""
    stub = scheduler.install(StubScheduler({"schedule": {"00-24": "YYYYYPP"}}))
    prime_plugin(fleet, stub)
    app = build(config_dir)[0]

    async def run():
        async with asgi_client(app) as client:
            client.cookies.set(i18n.COOKIE_NAME, "de")
            return await client.get("/fleet/Root-1")

    page = asyncio.run(run())
    assert page.status_code == 200
    assert "Der Wochenplan" in page.text, "the block's label is not German"
    assert "Wann er läuft" in page.text, "the block's heading is not German"
    assert '<i class="on"></i>läuft' in page.text, "the legend's run word is not German"
    assert "fährt ohne Spieler herunter" in page.text
