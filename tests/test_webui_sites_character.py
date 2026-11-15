"""T10's rendering half: the declared character decides the band's order and the board's opening.

WHAT THIS SUITE PINS
--------------------
* a ``pve`` site's pilot page leads with what its pilots FLEW (sorties, then playtime) and prints
  no ``N in PvP`` note on the air-kills cell, because on a PvE site that note is a zero;
* a ``pvp`` site leads with the player-versus-player figures and keeps the note, and ``pvpve`` —
  the default, and what a site that declares nothing renders — keeps today's order and today's
  note unchanged;
* the leaderboard opens on the character's first choice AMONG THE SITE'S OFFERED METRICS, falls
  through to the site's first offered metric when the character's own are switched off, and never
  opens on a board the site does not rank;
* the character is presentation only: the pilot page runs the SAME statements whatever it declares.
"""
from __future__ import annotations

import asyncio
import html
import types
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import mount, sites
from services.webservice.pages import site_leaderboard, site_player

REPO = Path(__file__).resolve().parent.parent

SERVERS_YAML = """\
Root-1:
  port: 10308
"""

#: one pilot's figures. The air-kills note states ``pvp`` of the ``kills_planes + kills_helicopters``
#: total, which is what a PvE site withholds.
TOTALS = {"playtime": 812 * 3600 + 1800, "sessions": 9, "kills": 30, "pvp": 3, "deaths": 4,
          "teamkills": 0, "takeoffs": 20, "landings": 18, "crashes": 1, "ejections": 1,
          "kills_planes": 7, "kills_helicopters": 5, "kills_ground": 6, "kills_sams": 2,
          "deaths_pvp": 2}

#: the SQL each leaderboard metric is ranked by, so the page's opening board is readable from the
#: statement the read was given.
METRIC_SQL = {
    "playtime": "SUM(ms.playtime)",
    "air_targets": "SUM(ms.kills_planes + ms.kills_helicopters)",
    "ground_targets": "SUM(ms.kills_ground)",
    "kd_ratio": "SUM(ms.kills::DECIMAL)",
    "pvp_kd_ratio": "SUM(ms.pvp::DECIMAL)",
}

#: the strip's cells, in the order a ``pvpve`` site prints them today — the compatibility promise.
TODAY = ["Playtime", "Sorties", "Air kills", "PvP kills", "K/D"]


# --------------------------------------------------------------------------- doubles

class _Cursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, params))
        self._rows = iter(self._pool.answer(statement, params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None


class _Connection:
    def __init__(self, pool):
        self._pool = pool

    def cursor(self, **_kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class StubPool:
    """Answers the pilot-profile and leaderboard statements by marker; no rating tables exist."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def connection(self):
        return _Connection(self)

    def statements(self) -> list[str]:
        return [statement for statement, _params in self.calls]

    def board_statements(self) -> list[str]:
        return [statement for statement in self.statements()
                if "WITH page AS MATERIALIZED" in statement]

    def answer(self, statement, params):
        if "to_regclass" in statement:
            return [{"present": False}]
        if "SELECT p.ucid, p.name, p.discord_id" in statement:
            return [{"ucid": "U1", "name": "Special K", "discord_id": None}]
        if "AS ejections" in statement:
            return [dict(TOTALS)]
        if "s.slot AS aircraft" in statement:
            return [{"aircraft": "F-16C_50", "playtime": 812 * 3600, "sessions": 9}]
        if "m.server_name AS server_name," in statement:
            return [{"server_name": "Root-1", "playtime": 812 * 3600, "sessions": 9}]
        if "m.mission_name" in statement:
            return [{"hop_on": "2026-11-03T18:04:00", "slot": "F-16C_50", "server_name": "Root-1",
                     "mission_name": "Op. Northern Shield", "playtime": 6120}]
        if "WITH page AS MATERIALIZED" in statement:
            return [{"ucid": "u-1", "name": "Root One", "value": 3600, "playtime": 3600}]
        return []


class StubBus:
    def __init__(self):
        self.apool = StubPool()
        self.servers = ThreadSafeDict({"Root-1": types.SimpleNamespace(
            name="Root-1", locals={}, status=None, players={})})


@pytest.fixture
def bus():
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    previous = ServiceRegistry._singletons.get(ServiceBus)
    stub = StubBus()
    ServiceRegistry._singletons[ServiceBus] = stub
    try:
        yield stub
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous


@pytest.fixture(autouse=True)
def restore_console_mount():
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


def site_config(**root) -> dict:
    site = {"slug": "", "branding": {"title": "Root Site"}}
    site.update(root)
    return {"sites": {"root": site}}


def markup(config_dir, bus, path: str, **root) -> str:
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=site_config(**root))

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path)

    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    return response.text


def strip(page: str) -> list[tuple[str, str]]:
    """The readout band's cells in reading order: each cell's label and its note (``""`` if none)."""
    body = page.split('class="strip five"', 1)[1].split("</div>\n  </div>", 1)[0]
    cells: list[tuple[str, str]] = []
    for chunk in body.split('<div class="readout">')[1:]:
        label = html.unescape(chunk.split('class="k">', 1)[1].split("<", 1)[0].strip())
        note = ""
        if 'class="q">' in chunk:
            note = html.unescape(chunk.split('class="q">', 1)[1].split("<", 1)[0].strip())
        cells.append((label, note))
    return cells


def labels(page: str) -> list[str]:
    return [label for label, _note in strip(page)]


def note_of(page: str, label: str) -> str:
    return dict(strip(page))[label]


# --------------------------------------------------------------------- the band's order and note

def test_a_pve_site_leads_with_what_its_pilots_flew_and_drops_the_pvp_note(config_dir, bus):
    page = markup(config_dir, bus, "/player/U1", character="pve")
    assert labels(page) == ["Sorties", "Playtime", "Air kills", "PvP kills", "K/D"]
    assert "in PvP" not in page, "a PvE site still prints the PvP note"
    assert note_of(page, "Air kills") == "", "the air-kills cell states a figure on a PvE site"
    # the figures themselves are unchanged: only the emphasis moved
    assert note_of(page, "Sorties") == "recorded takeoffs"
    assert note_of(page, "Playtime") == "on this site's servers"


def test_a_pvp_site_leads_with_its_combat_figures_and_keeps_the_note(config_dir, bus):
    page = markup(config_dir, bus, "/player/U1", character="pvp")
    assert labels(page) == ["PvP kills", "K/D", "Air kills", "Sorties", "Playtime"]
    assert "3 in PvP" in page, "a PvP site's air-kills note is meaningful and must stay"
    assert note_of(page, "Air kills") == "3 in PvP"


def test_a_pvpve_site_keeps_todays_order_and_todays_note(config_dir, bus):
    declared = markup(config_dir, bus, "/player/U1", character="pvpve")
    default = markup(config_dir, bus, "/player/U1")
    assert labels(declared) == TODAY
    assert strip(declared) == strip(default), "declaring the default changed the page"
    assert note_of(declared, "Air kills") == "3 in PvP"


def test_the_character_is_presentation_only_and_adds_no_read(config_dir, bus):
    """The same statements run for all three characters: only their order and one note differ."""
    runs = {}
    for character in sites.CHARACTERS:
        bus.apool.calls.clear()
        markup(config_dir, bus, "/player/U1", character=character)
        runs[character] = sorted(bus.apool.statements())
    assert runs["pvp"] == runs["pve"] == runs["pvpve"], "the character changed what is read"


def test_the_band_shows_the_same_five_figures_whatever_the_character(config_dir, bus):
    for character in sites.CHARACTERS:
        page = markup(config_dir, bus, "/player/U1", character=character)
        assert sorted(labels(page)) == sorted(TODAY), character
        assert "812" in page and "20" in page and "12" in page, character


# ---------------------------------------------------------------- the leaderboard's opening board

@pytest.mark.parametrize("character,metric", [("pvp", "pvp_kd_ratio"),
                                              ("pve", "air_targets"),
                                              ("pvpve", "playtime")])
def test_the_board_opens_on_the_characters_first_choice(config_dir, bus, character, metric):
    markup(config_dir, bus, "/rankings", character=character)
    statements = bus.apool.board_statements()
    assert len(statements) == 1
    assert METRIC_SQL[metric] in statements[0]


@pytest.mark.parametrize("character,metrics,metric",
                         [("pvp", ["kd_ratio"], "kd_ratio"),
                          ("pve", ["ground_targets"], "ground_targets"),
                          ("pve", ["ships", "ground_targets"], "ground_targets"),
                          ("pvpve", ["air_targets", "playtime"], "playtime")])
def test_the_two_settings_compose(config_dir, bus, character, metrics, metric):
    """The character's list is tried IN the site's offered set; what is missing falls through."""
    markup(config_dir, bus, "/rankings", character=character, metrics=metrics)
    statements = bus.apool.board_statements()
    assert METRIC_SQL[metric] in statements[0], f"{character} over {metrics} did not open on {metric}"


@pytest.mark.parametrize("character", sites.CHARACTERS)
def test_a_site_that_offers_no_character_metric_opens_on_its_first(config_dir, bus, character):
    markup(config_dir, bus, "/rankings", character=character, metrics=["ships", "air_defence"])
    statements = bus.apool.board_statements()
    assert "SUM(ms.kills_ships)" in statements[0], f"{character} did not fall through to the site's own"


def test_the_metric_control_offers_the_sites_own_boards_only(config_dir, bus):
    page = markup(config_dir, bus, "/rankings", metrics=["air_targets", "ground_targets"])
    assert "Air Targets" in page and "Ground Targets" in page
    assert "Longest Playtimes" not in page


def test_the_page_states_the_opening_board_it_actually_served(config_dir, bus):
    """The control's selected entry and the panel's heading are the board the read answered."""
    page = markup(config_dir, bus, "/rankings", character="pve", metrics=["ships", "air_targets"])
    assert site_leaderboard.METRIC_MSGIDS["air_targets"] in page
    assert site_leaderboard.METRIC_MSGIDS["playtime"] not in page
