"""The public site's overview section switches and the boards a site offers.

WHAT THIS SUITE PINS
--------------------
* T6 — a switched-off section is neither RENDERED nor READ: the pin asserts on the statements the
  pool recorded, not only on the markup, and the sections below it keep a contiguous number;
* T7 — the section vocabulary, the template's branch points and the schema's ``sections:`` keys are
  the same seven ids, so a section cannot exist without a switch and a switch cannot exist without
  a section;
* the aircraft and when-we-fly panels share one row: it renders when EITHER is on, vanishes when
  both are off, and the surviving panel takes the row's whole width;
* a site that offers a subset of the leaderboard metrics renders a control for those only, and a
  stale ``?metric=`` for a board it does not offer opens the site's own board — never an empty one.
"""
from __future__ import annotations

import asyncio
import re
import types
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import mount, sites
from services.webservice.pages import site_overview

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
TEMPLATE_PATH = REPO / "services" / "webservice" / "templates" / "site_overview.html"

SERVERS_YAML = """\
Root-1:
  port: 10308
Group-1:
  managed_by:
    - 5th Guards
"""

#: one sentinel per read, in a range no other figure on the page uses.
FIGURES = {"known": 4242, "playtime": 131 * 3600, "sessions": 17, "avg": 3600, "sorties": 4343,
           "missions": 9, "active": 5, "hours": 13, "aircraft": "Sentinel Jet",
           "aircraft_sorties": 47, "pilot": "Sentinel Pilot", "peak_hour": 20, "peak_dow": 6}

#: the statement marker of each scoped read this page can run — the pin for "the read is not run"
#: reads THIS table, so a sentence that is never built is proven by the statement it would be.
READ_MARKERS = {
    "community_totals": "AS known_pilots",
    "activity_over_time": "TO_CHAR(week_start",
    "server_board": "FROM mv_serverstats",
    "standings": "FROM mv_site_pilot_totals",
    "squadrons": "WITH site_players AS",
    "aircraft": "FROM mv_site_aircraft",
    "hours_of_day": "FROM mv_site_hours",
}

#: the SQL each leaderboard metric is ranked by, so the page's opening board is readable from the
#: statement the read was given.
METRIC_SQL = {
    "playtime": "SUM(ms.playtime)",
    "air_targets": "SUM(ms.kills_planes + ms.kills_helicopters)",
    "ground_targets": "SUM(ms.kills_ground)",
    "pvp_kd_ratio": "SUM(ms.pvp::DECIMAL)",
}

SECTION_NUMBER = re.compile(r'class="no">(\d\d)<')


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
    """A pool that RECORDS every statement it is given and answers the page's reads."""

    def __init__(self, figures=None):
        self.calls: list[tuple[str, dict]] = []
        self.figures = dict(figures or FIGURES)

    def connection(self):
        return _Connection(self)

    def statements(self) -> list[str]:
        return [statement for statement, _params in self.calls]

    def ran(self, read: str) -> bool:
        """Whether the statement of *read* was run at all."""
        return any(READ_MARKERS[read] in statement for statement in self.statements())

    def answer(self, statement, params):
        figures = self.figures
        if "AS known_pilots" in statement:
            return [{"known_pilots": figures["known"], "total_playtime": figures["playtime"],
                     "sessions": figures["sessions"], "avg_session": figures["avg"],
                     "sorties": figures["sorties"], "missions_flown": figures["missions"],
                     "active_this_week": figures["active"],
                     "active_last_week": figures["active"] - 1}]
        if "TO_CHAR(week_start" in statement:
            return [{"week": "2026-W44", "playtime": figures["playtime"],
                     "pilots": 3, "sorties": figures["sorties"]}]
        if "FROM mv_serverstats" in statement:
            return [{"server_name": "Root-1", "playtime": figures["playtime"],
                     "sorties": figures["sorties"]}]
        if "FROM mv_site_pilot_totals" in statement:
            return [{"ucid": "u-1", "name": figures["pilot"], "value": figures["playtime"],
                     "playtime": figures["playtime"]}]
        if "FROM mv_site_aircraft" in statement:
            return [{"slot": figures["aircraft"], "playtime": figures["playtime"],
                     "sorties": figures["aircraft_sorties"]}]
        if "FROM mv_site_hours" in statement:
            if "GROUP BY hour" in statement:
                return [{"hour": figures["peak_hour"], "seconds": figures["hours"] * 3600,
                         "sessions": 1}]
            return [{"dow": figures["peak_dow"], "seconds": figures["hours"] * 3600,
                     "sessions": 1}]
        if "WITH site_players AS" in statement:
            return []
        if "to_regclass" in statement:
            return [{"present": False}]
        return []


class StubBus:
    """The in-process service bus: a recording pool and the live server registry."""

    def __init__(self):
        self.apool = StubPool()
        self.servers = ThreadSafeDict({"Root-1": types.SimpleNamespace(
            name="Root-1", locals={}, status=None, players={})})


@pytest.fixture
def bus():
    """Install a stub ``ServiceBus`` singleton for the duration of one test."""
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
    """``mount``'s prefix is process state; a site install must not leak into the next test."""
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
    """The root site's block, with whatever the test declares on top of the shell's defaults."""
    site = {"slug": "", "branding": {"title": "Root Site"}}
    site.update(root)
    return {"sites": {"root": site}}


def build(config_dir, config: dict):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    return app


def get(config_dir, bus, path: str = "/", **root):
    """One page of the root site through the real shell; returns the whole HTTP response."""
    app = build(config_dir, site_config(**root))

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path)

    return asyncio.run(run())


def markup(config_dir, bus, path: str = "/", **root) -> str:
    response = get(config_dir, bus, path, **root)
    assert response.status_code == 200, response.text
    return response.text


def fresh(bus) -> None:
    """Forget the statements of the previous render: a test may render the page several times."""
    bus.apool.calls.clear()


def section_numbers(page: str) -> list[str]:
    """The section numbers the page prints, in the order it prints them."""
    return SECTION_NUMBER.findall(page)


# --------------------------------------------------------------------- T6: off is off, and unread

def test_a_switched_off_section_renders_nothing_and_runs_no_statement(config_dir, bus):
    """``sections: {standings: false}``: no band, no statement, and no hole in the numbering."""
    page = markup(config_dir, bus, sections={"standings": False})

    assert 'id="standings"' not in page
    assert "The standings" not in page
    assert "Who has flown the most on these servers." not in page
    assert f">{FIGURES['pilot']}<" not in page, "the standings' own row is on the page"

    # THE PIN THE SWITCH IS FOR: the read is not run, not merely unused.
    assert not bus.apool.ran("standings"), "the standings read ran for a section that is off"
    # … and the reads of the sections that ARE on still ran, so the assertion above is about the
    # switch and not about the page having failed earlier.
    for still_on in ("community_totals", "activity_over_time", "server_board", "aircraft",
                     "hours_of_day"):
        assert bus.apool.ran(still_on), f"'{still_on}' did not run on a site that shows it"

    # the sections below the missing one move up: 01 02 03 04, with no gap where standings was
    assert section_numbers(page) == ["01", "02", "03", "04"]


def test_the_hero_figure_survives_every_switch(config_dir, bus):
    """A switch hides a band, never the hero figure or the live band above it."""
    everything_off = {key: False for key in sites.SECTION_IDS}
    page = markup(config_dir, bus, sections=everything_off)

    assert f">{FIGURES['playtime'] // 3600}<" in page, "the hero's hours are the page's own figure"
    assert "servers up" in page and "pilots this week" in page, "the live band is not a section"
    assert section_numbers(page) == [], "every section is off, so no band prints a number"

    # the two reads the bands above the sections need still ran; the rest did not
    assert bus.apool.ran("community_totals") and bus.apool.ran("server_board")
    for off in ("activity_over_time", "standings", "squadrons", "aircraft", "hours_of_day"):
        assert not bus.apool.ran(off), f"'{off}' ran for a section that is off"


#: the heading each switch's band prints, so one switch can be followed on the page it hides.
HEADINGS = {
    "community": "This community",
    "season": "The season so far",
    "standings": "The standings",
    "aircraft": "Most flown",
    "when_we_fly": "When we fly",
    "servers": "The servers",
}


@pytest.mark.parametrize("key", sorted(HEADINGS))
def test_switching_one_section_off_hides_that_band_and_no_other(config_dir, bus, key):
    """One switch, one band. ``squadrons`` is not here: it also needs the CreditSystem plugin."""
    full = markup(config_dir, bus)
    assert HEADINGS[key] in full, f"'{key}' is not on a page that shows everything"

    fresh(bus)
    page = markup(config_dir, bus, sections={key: False})
    assert HEADINGS[key] not in page, f"'{key}' is still rendered"
    for other, heading in HEADINGS.items():
        if other != key:
            assert heading in page, f"switching '{key}' off took '{other}' with it"


def test_a_switch_does_not_reach_another_site(config_dir, bus):
    """The switches are per site: the group's site keeps its own defaults."""
    config = site_config(sections={"standings": False})
    config["sites"]["group1"] = {"slug": "group1", "group_role": "5th Guards",
                                "branding": {"title": "Group"}}
    app = build(config_dir, config)

    async def root():
        async with asgi_client(app) as client:
            return await client.get("/")

    async def group():
        async with asgi_client(app) as client:
            return await client.get("/group1")

    fresh(bus)
    asyncio.run(root())
    assert not bus.apool.ran("standings"), "the root site's switch did not apply"

    fresh(bus)
    asyncio.run(group())
    assert bus.apool.ran("standings"), "the group's site inherited the root site's switch"


# ------------------------------------------------- T7: vocabulary ↔ template ↔ schema, one set

def test_the_section_vocabulary_has_one_source():
    assert site_overview.SECTION_IDS == sites.SECTION_IDS
    assert sites.DEFAULT_SECTIONS == sites.SECTION_IDS


def test_every_section_id_has_a_branch_in_the_template():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key in sites.SECTION_IDS:
        assert f"model.sections.{key}" in source, f"the template never branches on '{key}'"
    branches = set(re.findall(r"model\.sections\.(\w+)", source))
    assert branches == set(sites.SECTION_IDS), "the template branches on an id the site has none of"


def test_the_schema_offers_exactly_the_section_vocabulary():
    schema = YAML(typ="safe").load(SCHEMA_PATH.read_text(encoding="utf-8"))
    site_block = schema["mapping"]["DEFAULT"]["mapping"]["sites"]["mapping"]["regex;(.+)"]
    declared = site_block["mapping"]["sections"]["mapping"]
    assert set(declared) == set(sites.SECTION_IDS)
    # every switch is a boolean an operator can write, and its prose says what it does
    for key, rule in declared.items():
        assert rule["type"] == "bool", key
        assert rule["desc"].strip(), key


def test_the_metrics_block_offers_exactly_the_metric_vocabulary():
    from services.webservice import site_reads

    schema = YAML(typ="safe").load(SCHEMA_PATH.read_text(encoding="utf-8"))
    metrics = schema["mapping"]["DEFAULT"]["mapping"]["sites"]["mapping"]["regex;(.+)"][
        "mapping"]["metrics"]
    assert metrics["type"] == "seq" and metrics["sequence"] == [{"type": "str"}]
    assert "character" in metrics["desc"], "the prose names the setting the fallback composes with"
    assert set(site_reads.LEADERBOARD_METRICS) == set(site_reads.METRIC_LABELS)


# ------------------------------------------------- the aircraft and when-we-fly share ONE row

def test_the_aircraft_row_renders_when_either_panel_is_on(config_dir, bus):
    both = markup(config_dir, bus)
    assert "Most flown" in both and "When we fly" in both
    assert both.count('class="panel c6"') >= 2, "the two panels share the row, side by side"

    fresh(bus)
    only_aircraft = markup(config_dir, bus, sections={"when_we_fly": False})
    assert "Most flown" in only_aircraft
    assert "When we fly" not in only_aircraft
    assert '<section class="panel c12">' in only_aircraft, "the surviving panel takes the row"
    assert not bus.apool.ran("hours_of_day"), "the hours panel is off, so its read is not run"
    assert bus.apool.ran("aircraft")

    fresh(bus)
    only_hours = markup(config_dir, bus, sections={"aircraft": False})
    assert "When we fly" in only_hours
    assert "Most flown" not in only_hours
    assert "Sentinel Jet" not in only_hours
    assert not bus.apool.ran("aircraft"), "the aircraft panel is off, so its read is not run"
    assert bus.apool.ran("hours_of_day")


def test_the_aircraft_row_is_gone_when_both_panels_are_off(config_dir, bus):
    page = markup(config_dir, bus, sections={"aircraft": False, "when_we_fly": False})
    for gone in ("Most flown", "When we fly", "The aircraft", "hours flown by hour",
                 "Sentinel Jet"):
        assert gone not in page, f"'{gone}' is still on the page"
    assert not bus.apool.ran("aircraft") and not bus.apool.ran("hours_of_day")
    # … and the numbering closes over the missing row instead of skipping a number
    assert section_numbers(page) == ["01", "02", "03", "04"]


def test_the_two_panels_of_the_row_share_one_number():
    numbers = site_overview._sections(sites.DEFAULT_SECTIONS)
    assert numbers["aircraft"] == numbers["when_we_fly"] == "04"
    assert numbers["squadrons"] == "05" and numbers["servers"] == "06"
    # a switched-off id answers no number at all, which is what the template branches on
    off = site_overview._sections(["community", "servers"])
    assert off["community"] == "01" and off["servers"] == "02"
    for key in ("season", "standings", "aircraft", "when_we_fly", "squadrons"):
        assert off[key] == "", key


# ------------------------------------------------------------- the boards a site offers

def test_the_metric_control_offers_only_the_sites_own_boards(config_dir, bus):
    page = markup(config_dir, bus, path="/rankings",
                  metrics=["air_targets", "ground_targets"], character="pve")
    assert "Air Targets" in page and "Ground Targets" in page
    assert "Longest Playtimes" not in page, "a board the site does not offer is advertised anyway"
    assert "Ships" not in page and "KD-Ratio" not in page


def test_a_site_that_disables_playtime_opens_on_the_characters_first_offered_metric(config_dir, bus):
    page = markup(config_dir, bus, path="/rankings",
                  metrics=["ground_targets", "air_targets"], character="pve")
    statements = [s for s in bus.apool.statements() if "WITH page AS MATERIALIZED" in s]
    assert len(statements) == 1
    assert METRIC_SQL["air_targets"] in statements[0], \
        "the board opened on something other than the character's first offered metric"


def test_a_character_whose_boards_are_all_off_opens_on_the_first_offered_metric(config_dir, bus):
    """The two settings compose: pvpve prefers playtime, and this site does not offer it."""
    page = markup(config_dir, bus, path="/rankings",
                  metrics=["ground_targets", "air_targets"], character="pvpve")
    statements = [s for s in bus.apool.statements() if "WITH page AS MATERIALIZED" in s]
    assert METRIC_SQL["ground_targets"] in statements[0], \
        "the site's own first offered metric did not win"
    assert "Ground Targets" in page


def test_a_pvp_site_opens_on_its_own_ratio(config_dir, bus):
    markup(config_dir, bus, path="/rankings",
           metrics=["playtime", "pvp_kd_ratio"], character="pvp")
    statements = [s for s in bus.apool.statements() if "WITH page AS MATERIALIZED" in s]
    assert METRIC_SQL["pvp_kd_ratio"] in statements[0]


def test_a_stale_link_to_a_board_the_site_does_not_offer_opens_its_own(config_dir, bus):
    markup(config_dir, bus, path="/rankings?metric=playtime",
           metrics=["air_targets"], character="pvp")
    statements = [s for s in bus.apool.statements() if "WITH page AS MATERIALIZED" in s]
    assert METRIC_SQL["air_targets"] in statements[0], \
        "a stale ?metric= opened a board the site does not rank"


def test_a_site_that_offers_every_metric_keeps_todays_default(config_dir, bus):
    markup(config_dir, bus, path="/rankings")
    statements = [s for s in bus.apool.statements() if "WITH page AS MATERIALIZED" in s]
    assert METRIC_SQL["playtime"] in statements[0]
    assert "Longest Playtimes" in markup(config_dir, bus, path="/rankings")
