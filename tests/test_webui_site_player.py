"""The public site's pilot page: the character, the statistics, the rating and the scope.

WHAT THIS SUITE PINS
--------------------
* the DECLARED character: three accepted values, ``pvpve`` when the key is absent, and a refusal
  naming the key, the value and the accepted set for anything else;
* that the character changes EMPHASIS only: the strip's cell order and the combat panels' order and
  width, while the same five figures and both panels are always rendered — plus the one-line
  statement the page prints so a visitor knows why those numbers lead;
* the TrueSkill block: rendered from the plugin's two tables when they exist, and GONE (no empty
  panel, the discipline panel spanning the full width) when the ``to_regclass`` probe says the
  tables are absent;
* a pilot with zeros everywhere — no PvP kills, no rating, no airframe hours — renders in full
  without raising;
* the site scope: a pilot with no session on the site's servers is not found (404 through the page),
  and every scoped statement binds the site's own server list.

The database is a stub pool: the statements are asserted for shape and scope, the figures for the
values the page composed out of them. Nothing here ran against a real database.
"""
from __future__ import annotations

import asyncio
import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import i18n, site_reads, sites
from services.webservice.pages import site_player

SERVERS_YAML = """\
Root-1:
  port: 10308
Group-1:
  managed_by:
    - 5th Guards
"""

#: A pilot with real numbers: 12 air targets, 3 PvP kills, 4 deaths (2 of them in PvP).
TOTALS = {"playtime": 812 * 3600 + 1800, "sessions": 9, "kills": 30, "pvp": 3, "deaths": 4,
          "teamkills": 0, "takeoffs": 20, "landings": 18, "crashes": 1, "ejections": 1,
          "kills_planes": 7, "kills_helicopters": 5, "kills_ground": 6, "kills_sams": 2,
          "deaths_pvp": 2}


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
    """Answers the pilot-profile statements by marker; ``tables`` decides the rating's existence."""

    def __init__(self, *, found=True, tables=(), totals=None, aircraft=None, servers=None,
                 sessions=None, rating=None, history=None):
        self.calls: list[tuple[str, dict]] = []
        self.tables = set(tables)
        self.found = found
        self.totals = dict(totals if totals is not None else TOTALS)
        self.aircraft = aircraft if aircraft is not None else [
            {"aircraft": "F-16C_50", "playtime": 500 * 3600, "sessions": 6},
            {"aircraft": "A-10C_2", "playtime": 312 * 3600, "sessions": 3}]
        self.servers = servers if servers is not None else [
            {"server_name": "Root-1", "playtime": 812 * 3600, "sessions": 9}]
        self.sessions = sessions if sessions is not None else [
            {"hop_on": "2026-11-03T18:04:00", "slot": "F-16C_50", "server_name": "Root-1",
             "mission_name": "Op. Northern Shield", "playtime": 6120}]
        self.rating = rating if rating is not None else {"skill_mu": 43.7, "skill_sigma": 4.1,
                                                        "time": "2026-11-01T20:00:00"}
        self.history = history if history is not None else [
            {"skill_mu": 40.0, "skill_sigma": 6.2, "time": "2025-05-04T20:12:00"},
            {"skill_mu": 42.0, "skill_sigma": 5.0, "time": "2026-04-02T19:05:00"}]

    def connection(self):
        return _Connection(self)

    def answer(self, statement, params):
        if "to_regclass" in statement:
            return [{"present": (params or {}).get("name") in self.tables}]
        if "SELECT p.ucid, p.name, p.discord_id" in statement:
            return ([{"ucid": "U1", "name": "Special K", "discord_id": None}] if self.found
                    else [])
        if "ORDER BY p.last_seen DESC NULLS LAST" in statement:
            return [{"ucid": "U2", "name": "K. (old account)"}]
        if "AS ejections" in statement:
            return [self.totals]
        if "s.slot AS aircraft" in statement:
            return list(self.aircraft)
        if "m.server_name AS server_name," in statement:
            return list(self.servers)
        if "m.mission_name" in statement:
            return list(self.sessions)
        if "FROM trueskill_hist" in statement:
            # the plugin's own union: the superseded rows plus the row ``trueskill`` holds now
            rows = [*self.history, self.rating]
            return sorted(rows, key=lambda row: str(row.get("time") or ""))
        if "FROM trueskill" in statement:
            return [self.rating]
        return []


def _server(name, managed_by=None):
    locals_ = {} if managed_by is None else {"managed_by": [managed_by]}
    return types.SimpleNamespace(name=name, locals=locals_, status=None, players={})


@pytest.fixture(autouse=True)
def restore_console_mount():
    from services.webservice import mount

    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture
def bus():
    """Install a stub ``ServiceBus`` whose pool a test fills in; restore the previous one."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    previous = ServiceRegistry._singletons.get(ServiceBus)
    stub = types.SimpleNamespace(
        apool=None,
        servers=ThreadSafeDict({"Root-1": _server("Root-1"),
                                "Group-1": _server("Group-1", "5th Guards")}))
    ServiceRegistry._singletons[ServiceBus] = stub
    try:
        yield stub
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


def site_config(character=None):
    root = {"slug": "", "branding": {"title": "Root Site"}}
    if character is not None:
        root["character"] = character
    return {"sites": {"root": root}}


def build(config_dir, config):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    return app


def fetch(config_dir, bus, pool, *, character=None, path="/player/U1", language=None):
    """Render one page of the root site through the real app; returns the whole HTTP response."""
    bus.apool = pool
    app = build(config_dir, site_config(character))

    async def run():
        async with asgi_client(app) as client:
            if language:
                client.cookies.set(i18n.COOKIE_NAME, language)
            return await client.get(path)

    return asyncio.run(run())


def markup(config_dir, bus, pool, **kwargs) -> str:
    response = fetch(config_dir, bus, pool, **kwargs)
    assert response.status_code == 200, response.text
    return response.text


def strip_keys(page: str) -> list[str]:
    """The strip's cells in reading order, read off the rendered labels."""
    labels = {"Playtime": "playtime", "Sorties": "sorties", "Air kills": "air_kills",
              "PvP kills": "pvp", "K/D": "kd"}
    body = page.split('class="strip five"', 1)[1].split("</div>\n  </div>", 1)[0]
    found = []
    for chunk in body.split('<div class="k">')[1:]:
        label = chunk.split("<", 1)[0].strip()
        if label in labels:
            found.append(labels[label])
    return found


# --------------------------------------------------------------------------- the character key

@pytest.mark.parametrize("value", ["pvp", "pve", "pvpve"])
def test_the_parser_accepts_the_three_characters(value):
    catalog = sites.parse_sites({"root": {"slug": "", "character": value}})
    assert catalog.root.character == value
    assert catalog.root.context().character == value


def test_an_absent_character_is_pvpve():
    assert sites.parse_sites({"root": {"slug": ""}}).root.character == sites.DEFAULT_CHARACTER
    assert sites.DEFAULT_CHARACTER == "pvpve"


@pytest.mark.parametrize("value", ["mixed", "PvP", 3, ["pvp"]])
def test_an_unknown_character_is_refused_and_the_message_names_key_value_and_set(value):
    with pytest.raises(sites.SiteConfigError) as caught:
        sites.parse_sites({"root": {"slug": "", "character": value}})
    message = str(caught.value)
    assert "sites.root.character" in message, message
    assert f"'{value}'" in message, message
    assert "pvp, pve, pvpve" in message, message
    assert "absent means 'pvpve'" in message, message


def test_the_character_is_not_inferred_from_the_data():
    """It is a config key: the same totals answer the same order, and nothing sniffs a share."""
    source = (site_player.__file__)
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "def _character(resolved" in text
    assert "getattr(resolved, \"character\"" in text, "the character must come from the site"


# --------------------------------------------------------------------------- the emphasis

def test_the_strip_shows_the_same_five_figures_for_every_character():
    totals = dict(TOTALS)
    orders = {key: [row["key"] for row in site_player.readouts(totals, key)]
              for key in sites.CHARACTERS}
    assert all(sorted(order) == ["air_kills", "kd", "playtime", "pvp", "sorties"]
               for order in orders.values()), orders
    assert orders["pvp"][0] == "pvp"
    assert orders["pve"][:2] == ["sorties", "playtime"], "a PvE site leads with what was flown"
    assert orders["pvpve"][0] == "playtime"


def test_the_combat_panels_lead_by_character():
    pvp = site_player.combat(dict(TOTALS), "pvp")
    pve = site_player.combat(dict(TOTALS), "pve")
    both = site_player.combat(dict(TOTALS), "pvpve")
    assert [panel["id"] for panel in pvp] == ["pvp", "targets"]
    assert [panel["id"] for panel in pve] == ["targets", "pvp"]
    assert [panel["id"] for panel in both] == ["pvp", "targets"]
    # the leading panel is the wider one; the balanced character puts them side by side
    assert [panel["span"] for panel in pvp] == [7, 5]
    assert [panel["span"] for panel in pve] == [7, 5]
    assert [panel["span"] for panel in both] == [6, 6]


def test_both_panels_carry_their_figures_whatever_leads():
    for character in sites.CHARACTERS:
        panels = {panel["id"]: panel for panel in site_player.combat(dict(TOTALS), character)}
        pvp_values = [figure["value"] for figure in panels["pvp"]["figures"]]
        targets = [figure["value"] for figure in panels["targets"]["figures"]]
        assert pvp_values == ["3", "1.50"], character
        assert targets == ["12", "6", "2"], character


def test_an_unknown_character_number_falls_back_to_the_default_order():
    assert site_player.readouts({}, "nonsense") == site_player.readouts({}, "pvpve")
    assert site_player.combat({}, "nonsense") == site_player.combat({}, "pvpve")


@pytest.mark.parametrize("character,line", [("pvp", "PvP community"),
                                           ("pve", "PvE community"),
                                           ("pvpve", "PvP and PvE community")])
def test_the_page_states_the_character_in_one_line(config_dir, bus, character, line):
    page = markup(config_dir, bus, StubPool(tables=("trueskill", "trueskill_hist")),
                  character=character)
    assert f'<span class="tag">{line}</span>' in page


def test_the_rendered_page_leads_with_the_declared_character(config_dir, bus):
    page = markup(config_dir, bus, StubPool(), character="pve")
    assert page.index("Targets hit") < page.index("PvP record")
    assert strip_keys(page) == ["sorties", "playtime", "air_kills", "pvp", "kd"]

    page = markup(config_dir, bus, StubPool(), character="pvp")
    assert page.index("PvP record") < page.index("Targets hit")
    assert strip_keys(page) == ["pvp", "kd", "air_kills", "sorties", "playtime"]

    page = markup(config_dir, bus, StubPool(), character="pvpve")
    assert page.index("PvP record") < page.index("Targets hit")
    assert page.count('class="panel c6"') == 2, "the balanced layout puts the panels side by side"
    assert strip_keys(page) == ["playtime", "sorties", "air_kills", "pvp", "kd"]


# --------------------------------------------------------------------------- the figures

def test_the_strip_renders_the_figures_the_read_returned(config_dir, bus):
    page = markup(config_dir, bus, StubPool())
    assert "812" in page, "playtime in whole hours"
    assert "20" in page, "sorties are the recorded takeoffs"
    assert "12" in page, "air kills are planes plus helicopters"
    assert "3 in PvP" in page, "the air-kills cell states how many of them a player flew"
    assert "0.15" in page, "PvP kills per sortie"
    assert "7.50" in page, "the overall K/D ratio"


def test_flight_discipline_rates_are_of_the_pilots_own_sorties():
    model = site_player.discipline(dict(TOTALS))
    rates = {row["label"]: row["rate"] for row in model["rows"]}
    assert rates["Takeoffs"] == 100.0
    assert rates["Landings"] == 90.0
    assert rates["Crashes"] == 5.0
    assert rates["Ejections"] == 5.0
    assert model["takeoffs"] == "20" and model["landings"] == "18"


def test_a_pilot_with_no_sortie_has_no_division_by_zero():
    model = site_player.discipline({})
    assert [row["rate"] for row in model["rows"]] == [0.0, 0.0, 0.0, 0.0]
    assert model["sorties"] == "0"


def test_the_most_flown_airframe_is_the_longest_one():
    assert site_player.most_flown([{"aircraft": "A", "playtime": 10},
                                   {"aircraft": "B", "playtime": 90}]) == "B"
    assert site_player.most_flown([]) == ""


def test_the_recent_sorties_table_carries_a_date_and_a_duration(config_dir, bus):
    page = markup(config_dir, bus, StubPool())
    assert "2026-11-03" in page
    assert "Op. Northern Shield" in page
    assert "Root-1" in page
    assert "1 h 42" in page, "6120 s is one hour and 42 minutes"


# --------------------------------------------------------------------------- TrueSkill

#: A synthetic three-point history: μ climbing then easing off, σ narrowing, ~18 months of span.
RATING_POINTS = [
    {"time": "2025-05-04T20:12:00", "mu": 20.0, "sigma": 6.0},
    {"time": "2026-04-02T19:05:00", "mu": 30.0, "sigma": 3.0},
    {"time": "2026-11-08T21:40:00", "mu": 27.4, "sigma": 4.1},
]


def rating_of(points: list[dict]) -> dict:
    """The read's rating contract for *points*, with the newest one as the current rating."""
    newest = points[-1] if points else {"mu": 0.0, "sigma": 0.0}
    return {"mu": newest["mu"], "sigma": newest["sigma"],
            "conservative": round(newest["mu"] - 3 * newest["sigma"], 2),
            "points": list(points), "count": len(points), "history": len(points) > 1}


def band_vertices(chart: dict) -> list[tuple[float, float]]:
    """The band path's vertices, in path order, read off its ``M``/``L`` commands."""
    return [(float(x), float(y)) for x, y in re.findall(r"[ML]([-\d.]+) ([-\d.]+)", chart["band"])]


def value_of(chart: dict, y: float) -> float:
    """The rating a percentage *y* on a flipped y axis stands for, through the chart's own domain."""
    bottom, top = chart["domain"]["bottom"], chart["domain"]["top"]
    return top - (y / 100.0) * (top - bottom)


def rating_panel(page: str) -> str:
    """The rating panel's own markup, from its id to its closing tag."""
    start = page.index('id="rating"')
    return page[start:page.index("</section>", start)]


def test_the_band_is_closed_and_its_edges_are_mu_plus_and_minus_sigma():
    chart = site_player.rating_view(rating_of(RATING_POINTS))["chart"]
    assert chart["band"].startswith("M") and chart["band"].endswith(" Z")
    vertices = band_vertices(chart)
    assert len(vertices) == 2 * len(RATING_POINTS), "one vertex per point on each edge"
    upper, lower = vertices[:len(RATING_POINTS)], vertices[len(RATING_POINTS):]
    tolerance = (chart["domain"]["top"] - chart["domain"]["bottom"]) * 0.01
    for (x, y), point in zip(upper, RATING_POINTS):
        assert abs(value_of(chart, y) - (point["mu"] + point["sigma"])) <= tolerance
    assert [x for x, _ in lower] == [x for x, _ in reversed(upper)], \
        "the lower edge runs newest back to oldest"
    for (x, y), point in zip(lower, reversed(RATING_POINTS)):
        assert abs(value_of(chart, y) - (point["mu"] - point["sigma"])) <= tolerance


def test_the_line_has_one_vertex_per_point_and_x_is_monotonic():
    chart = site_player.rating_view(rating_of(RATING_POINTS))["chart"]
    vertices = [tuple(float(part) for part in pair.split(","))
                for pair in chart["line"].split()]
    assert len(vertices) == len(RATING_POINTS)
    x = [position for position, _ in vertices]
    assert x == sorted(x) and x[0] == 0.0 and x[-1] == 100.0
    assert chart["point"]["x"] == "100.00", "the marker sits on the newest point"


def test_the_dates_and_sentences_follow_the_pages_language(config_dir, bus):
    panel = rating_panel(markup(config_dir, bus,
                                StubPool(tables=("trueskill", "trueskill_hist")),
                                language="de"))
    assert "Mai 2025" in panel and "April 2026" in panel and "November 2026" in panel
    assert "Seit 4. Mai 2025 um 3.7 Punkte gestiegen." in panel
    assert "Über diesen Zeitraum um 3.7 Punkte gestiegen." in panel


def test_the_rating_block_renders_from_the_plugins_tables(config_dir, bus):
    page = markup(config_dir, bus, StubPool(tables=("trueskill", "trueskill_hist")))
    assert "Competitive rating" in page
    assert "31.4" in page, "μ − 3σ, the value the plugin's own command prints"
    assert "σ 4.1" in page
    assert "43.7 μ · 3 rating updates" in page
    panel = rating_panel(page)
    assert '<figure class="rc">' in panel
    assert '<path class="rc-band"' in panel and '<polyline class="rc-line"' in panel
    assert 'class="panel c7"' in page, "the discipline panel narrows when the rating is shown"


def test_the_chart_carries_the_accessibility_sentence_and_the_caption(config_dir, bus):
    panel = rating_panel(markup(config_dir, bus,
                                StubPool(tables=("trueskill", "trueskill_hist"))))
    assert 'role="img"' in panel
    assert "Rating 43.7, confidence plus or minus 4.1." in panel
    assert "Up 3.7 points since 4 May 2025." in panel
    assert "μ is the expected rating; the band is ±σ, the confidence around it." in panel
    assert "Up 3.7 points across this span." in panel
    assert '<div class="rc-legend" aria-hidden="true">' in panel


def test_the_block_is_gone_when_the_tables_are_absent(config_dir, bus):
    page = markup(config_dir, bus, StubPool(tables=()))
    assert "Competitive rating" not in page
    assert "TrueSkill" not in page
    assert 'class="rc' not in page, "a missing table leaves no empty shell"
    assert 'class="panel c12"' in page, "the discipline panel spans the full width instead"


def test_the_panel_is_absent_when_the_read_answers_no_rating():
    assert site_player.rating_view(None) is None
    assert site_player.rating_view({}) is None
    assert site_player.rating_view({"conservative": None}) is None


def test_a_rating_without_recorded_history_shows_the_value_and_no_line(config_dir, bus):
    page = markup(config_dir, bus, StubPool(tables=("trueskill", "trueskill_hist"),
                                            history=[]))
    panel = rating_panel(page)
    assert "Competitive rating" in page and "31.4" in page
    assert "43.7 μ · 1 rating updates" in panel
    assert "<svg" not in panel and "rc-band" not in panel and "rc-line" not in panel
    assert "rc-whisk" in panel, "one reading is a ±σ bar, not a line across time"
    assert "No earlier values exist, so there is no trend to draw." in panel
    assert "1 November 2026" in panel, "the one-point label is the full date"


def test_the_one_point_model_draws_no_line_and_no_band():
    view = site_player.rating_view(rating_of([{"time": "2026-11-08T21:40:00",
                                               "mu": 27.4, "sigma": 4.1}]))
    assert view["chart"] is None and view["single"] is not None
    assert view["single"]["bar"]["top"] == "5.00" and view["single"]["bar"]["height"] == "82.00"


def test_the_empty_state_renders_the_sentence_and_no_svg(config_dir, bus):
    # a ``trueskill`` row with no timestamp is the one shape that yields no point at all
    pool = StubPool(tables=("trueskill", "trueskill_hist"),
                    rating={"skill_mu": 43.7, "skill_sigma": 4.1, "time": None}, history=[])
    panel = rating_panel(markup(config_dir, bus, pool))
    assert "No rating update has been recorded yet." in panel
    assert "<svg" not in panel and "<figure" not in panel
    assert "43.7 μ" in panel and "rating updates" not in panel, "a count of zero is not printed"


def test_the_empty_state_model_omits_the_count():
    view = site_player.rating_view(rating_of([]))
    assert view["empty"] is True and view["chart"] is None and view["single"] is None
    assert view["note"]["msgid"] == "{mu} μ" and view["note"]["params"] == {"mu": "0.0"}


def test_a_pilot_with_no_rating_row_renders_no_block(config_dir, bus):
    pool = StubPool(tables=("trueskill", "trueskill_hist"), rating={}, history=[])
    page = markup(config_dir, bus, pool)
    assert "Competitive rating" not in page


def test_the_ticks_are_three_and_cover_the_band():
    chart = site_player.rating_view(rating_of(RATING_POINTS))["chart"]
    assert [tick["text"] for tick in chart["ticks"]] == ["10", "30", "50"]
    assert [tick["top"] for tick in chart["ticks"]] == ["100.00", "50.00", "0.00"]


def test_the_dates_are_three_on_a_long_span_and_carry_the_halfway_point():
    chart = site_player.rating_view(rating_of(RATING_POINTS))["chart"]
    assert [label["place"] for label in chart["labels"]] == ["first", "mid", "last"]
    assert [label["pattern"] for label in chart["labels"]] == \
        ["May {year}", "April {year}", "November {year}"]
    assert [label["at"] for label in chart["labels"]] == ["0.00", "60.20", "100.00"]


def test_a_short_span_labels_day_and_month():
    chart = site_player.rating_view(rating_of([
        {"time": "2026-10-01T10:00:00", "mu": 20.0, "sigma": 6.0},
        {"time": "2026-11-08T21:40:00", "mu": 27.4, "sigma": 4.1}]))["chart"]
    assert [label["pattern"] for label in chart["labels"]] == ["{day} October", "{day} November"]


# --------------------------------------------------------------------------- the sparse pilot

def test_a_pilot_with_nothing_renders_in_full(config_dir, bus):
    empty = {"playtime": 0, "sessions": 0, "kills": 0, "pvp": 0, "deaths": 0, "takeoffs": 0,
             "landings": 0, "crashes": 0, "ejections": 0, "deaths_pvp": 0}
    pool = StubPool(totals=empty, aircraft=[], servers=[], sessions=[], tables=())
    page = markup(config_dir, bus, pool)
    assert "Special K" in page
    assert strip_keys(page) == ["playtime", "sorties", "air_kills", "pvp", "kd"]
    assert "No session has been recorded for this pilot on this site's servers yet." in page
    assert "Competitive rating" not in page


# --------------------------------------------------------------------------- the scope

def test_a_pilot_outside_the_sites_servers_is_not_found(config_dir, bus):
    response = fetch(config_dir, bus, StubPool(found=False))
    assert response.status_code == 404
    assert "No pilot answers to this address" in response.text


def test_every_scoped_statement_binds_the_sites_servers(config_dir, bus):
    pool = StubPool(tables=("trueskill", "trueskill_hist"))
    markup(config_dir, bus, pool)
    scoped = [(statement, params) for statement, params in pool.calls
              if site_reads.SERVER_FILTER in statement]
    assert len(scoped) >= 4, "the identity, totals, airframes, servers and sessions reads"
    for _statement, params in scoped:
        assert params["servers"] == ["Root-1"]
    # the two statements that carry no site filter are named, and are the rating and its probe
    unscoped = [statement for statement, _params in pool.calls
                if site_reads.SERVER_FILTER not in statement]
    assert unscoped, "the probes ran"
    for statement in unscoped:
        assert "to_regclass" in statement or "FROM trueskill" in statement, statement


def test_the_pilot_totals_carry_the_new_figures(config_dir, bus):
    pool = StubPool()
    markup(config_dir, bus, pool)
    statement = next(text for text, _params in pool.calls if "AS ejections" in text)
    for column in ("kills_planes", "kills_helicopters", "kills_ground", "kills_sams",
                   "deaths_pvp"):
        assert f"AS {column}" in statement, column


# --------------------------------------------------------------------------- the read itself

def _root_site():
    return types.SimpleNamespace(name="root", servers=frozenset({"Root-1"}))


def test_the_read_answers_no_rating_when_the_plugin_table_is_absent():
    pool = StubPool(tables=())
    result = asyncio.run(site_reads.pilot_profile(_root_site(), pool, "U1"))
    assert result["found"] is True
    assert result["rating"] is None


def test_the_read_answers_the_rating_and_its_recorded_points():
    pool = StubPool(tables=("trueskill", "trueskill_hist"))
    result = asyncio.run(site_reads.pilot_profile(_root_site(), pool, "U1"))
    rating = result["rating"]
    assert rating["conservative"] == 31.4 and rating["mu"] == 43.7 and rating["sigma"] == 4.1
    assert rating["count"] == 3 and rating["history"] is True
    assert [point["mu"] for point in rating["points"]] == [40.0, 42.0, 43.7], \
        "the points read oldest first"
    assert [point["sigma"] for point in rating["points"]] == [6.2, 5.0, 4.1]
    series = next(text for text, _params in pool.calls if "FROM trueskill_hist" in text)
    assert "UNION" in series and "ORDER BY time ASC" in series, \
        "the series is the plugin's own union, oldest first"
    assert "LIMIT" not in series


def test_the_read_does_not_merge_the_linked_ucids_rating():
    """A rating belongs to one UCID; merging two would invent a value no match produced."""
    pool = StubPool(tables=("trueskill", "trueskill_hist"))
    asyncio.run(site_reads.pilot_profile(_root_site(), pool, "U1"))
    statements = [(text, params) for text, params in pool.calls if "FROM trueskill" in text
                  and "hist" not in text]
    assert [params["ucid"] for _text, params in statements] == ["U1"]


def test_the_read_is_not_found_for_a_pilot_outside_the_site():
    pool = StubPool(found=False)
    result = asyncio.run(site_reads.pilot_profile(_root_site(), pool, "U1"))
    assert result["found"] is False and result["rating"] is None
    identity = next(params for text, params in pool.calls if "SELECT p.ucid, p.name" in text)
    assert identity["servers"] == ["Root-1"], "the scope probe binds the site's servers"
