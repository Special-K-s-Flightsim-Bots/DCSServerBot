"""The Statistics page at ``/statistics``: the strategic, period-based view.

One section per acceptance criterion:

* the ROUTE is registered and gated — 200 for an Admin, 403 for a role without the capability,
  driven through the REAL application (a template render would prove nothing about ``_core_router``);
* the NAV entry is offered to exactly the roles the route accepts, and sits where the menu says;
* the page says DISTINCT is not summable, and the KPI's qualifier carries the word;
* every section renders the FIXTURE's own values (no literal figure in the template);
* an EMPTY read renders one sentence and no figures;
* the page carries no live figure and no node fact;
* every string is wrapped: the German render shows German, not the English msgid.

The read is stubbed at the page's own seam (``pages.statistics.read_action``): the SQL and the scope
rules belong to the action, so what this file pins is the PAGE.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from core.action_results import ActionResult
from conftest import asgi_client, create_app, install_shell, stub_identity
from services.webservice import i18n, permissions
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import statistics as page
from services.webservice.shell import install_shell as shell_install
from test_webui_write_pause import Cluster, fleet, server  # noqa: F401 - the console's source doubles

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "services" / "webservice" / "templates" / "statistics.html"
MODULE = REPO / "services" / "webservice" / "pages" / "statistics.py"

#: The two servers the scope links must offer, from the console's own source double.
HOSTED = ("SRS-1", "SRS-B")

#: the static-asset fingerprint every ``<link>``/``<script>`` address carries, stripped before a
#: figure is looked for in the document
ASSET_STAMP = re.compile(r"\?v=\d+")


def payload(**overrides) -> dict:
    """One ``get_server_statistics`` answer in the action's own shape, every row distinct."""
    data = {
        "period": "month", "server": None, "window_days": page.RETENTION_DAYS,
        "unique_users_grain": "day",
        "usage": [
            {"server_name": "SRS-1", "playtime": 384, "players": 72, "members": 60,
             "sessions": 410, "missions": 31, "theatre": "Caucasus", "new_players": 17},
            {"server_name": "TRAINING", "playtime": 312, "players": 61, "members": 55,
             "sessions": 333, "missions": 24, "theatre": "Nevada", "new_players": 9},
        ],
        "theatres": [{"mission_theatre": "Syria", "playtime": 242},
                     {"mission_theatre": "Caucasus", "playtime": 214}],
        "missions": [{"mission_name": "Through the Inferno", "playtime": 214},
                     {"mission_name": "Operation Piercing Frost", "playtime": 168}],
        "modules": [{"slot": "F-16C_50", "num_usage": 120, "playtime": 268, "players": 64},
                    {"slot": "FA-18C_hornet", "num_usage": 96, "playtime": 241, "players": 58}],
        "unique_users": [{"date": "2026-10-28", "total_players": 78, "new_players": 9},
                         {"date": "2026-10-29", "total_players": 64, "new_players": 4}],
        "retention": [{"week": "2026-W41", "new_users": 12, "retained_users": 5},
                      {"week": "2026-W42", "new_users": 20, "retained_users": 9}],
        "day_time": [{"weekday": 1, "hour": 19, "players": 44},
                     {"weekday": 2, "hour": 19, "players": 40},
                     {"weekday": 3, "hour": 9, "players": 12}],
        "frequency": [{"week": "2026-W41", "sessions": 240, "players": 61},
                      {"week": "2026-W42", "sessions": 300, "players": 55}],
        "cluster": {"playtime": 1284, "players_distinct": 187, "new_players": 41,
                    "returning_rate": 0.38, "avg_session": 1.37},
    }
    data.update(overrides)
    return data


@pytest.fixture
def app():
    """The shell on a stub cluster of two servers — the scope links' own source."""
    application = create_app()
    install_shell(application, node=types.SimpleNamespace(master=True), config={})
    application.state.webui_source_provider = fleet().source
    return application


def stub_read(monkeypatch, data=None, *, success=True, calls=None):
    """Replace the page's action seam, recording every call's parameters."""
    calls = calls if calls is not None else []

    async def _read(qualname, ctx, /, **params):
        calls.append({"action": qualname, "params": params})
        return ActionResult(success=success, data=data)

    monkeypatch.setattr(page, "read_action", _read)
    return calls


async def fetch(application, path=page.STATISTICS_PATH, *, roles=frozenset({"Admin"}),
                cookies=None):
    """GET *path* through the real ASGI app as *roles*, returning the httpx response."""
    stub_identity(roles)
    async with asgi_client(application) as client:
        if cookies:
            for name, value in cookies.items():
                client.cookies.set(name, value)
        return await client.get(path)


# ------------------------------------------------------------------------ reachability, through the app

@pytest.mark.asyncio
async def test_the_route_answers_200_for_an_admin_and_403_without_the_capability(app, monkeypatch):
    """The route exists because ``_core_router`` registers it — and it is Admin-only."""
    stub_read(monkeypatch, payload())
    assert (await fetch(app, roles=frozenset({"Admin"}))).status_code == 200
    for roles in (frozenset({"DCS Admin"}), frozenset({"DCS"}), frozenset()):
        response = await fetch(app, roles=roles)
        assert response.status_code == 403, f"roles {sorted(roles) or ['<none>']} reached the page"


def test_the_nav_entry_is_offered_to_the_admin_and_nobody_else(app):
    """A nav item and its route read ONE declaration: the item is offered exactly where the route
    answers 200, and it sits between Nodes and Players in the shell's own registration order."""
    registrar = app.state.webui_registrar
    urls = [item.url for item in registrar.nav]
    assert urls.index(page.STATISTICS_PATH) == urls.index("/nodes") + 1, (
        f"Statistics does not sit after Nodes: {urls}")
    assert page.STATISTICS_PATH in [item.url
                                    for item in permissions.visible_nav(registrar.nav, {"Admin"})]
    assert page.STATISTICS_PATH not in [
        item.url for item in permissions.visible_nav(registrar.nav, {"DCS Admin"})]
    assert registrar.capability_for(path=page.STATISTICS_PATH) == page.STATISTICS_CAPABILITY


# ------------------------------------------------------------------------ distinct is not summable

@pytest.mark.asyncio
async def test_the_page_says_the_unique_total_is_distinct_and_not_the_column_sum(app, monkeypatch):
    """The KPI carries "distinct" and the table carries the sentence — with both numbers on the
    page, so the reader can see that 187 is not 72 + 61."""
    stub_read(monkeypatch, payload())
    body = (await fetch(app)).text

    assert str(page.DISTINCT_NOTE) in body
    assert str(page.NOTE_DISTINCT_SCOPE) in body
    assert "distinct" in page.NOTE_DISTINCT_SCOPE.lower()
    assert "187" in body and "133" in body, "the two totals the sentence is about are not both shown"


# ------------------------------------------------------------------------ the sections

@pytest.mark.asyncio
async def test_every_section_renders_the_fixtures_own_values(app, monkeypatch):
    """Every block shows the figures the fixture carries — computed, never typed into the template."""
    stub_read(monkeypatch, payload())
    body = (await fetch(app)).text

    # the KPI tiles
    assert "1,284 h" in body, "playtime"
    assert "1:22" in body, "average session (1.37 h)"
    assert "187" in body, "unique players, distinct"
    assert "41" in body, "new players"
    assert "38 %" in body, "returning rate"
    # players per day: the two days on the axis (MM-DD), their totals above the columns, and the
    # full date in each column's tooltip
    for text in ("10-28", "10-29", "78", "64", "2026-10-28"):
        assert text in body, f"players per day: {text}"
    # retention: the week and its share (5 of 12 -> 42 %, 9 of 20 -> 45 %)
    assert "2026-W41" in body and "42 %" in body, "retention share"
    assert "2026-W42" in body and "45 %" in body, "retention share"
    # frequency: 240/61 -> 3.9, 300/55 -> 5.5
    assert "3.9" in body and "5.5" in body, "sessions per active player"
    # when they fly: the peak hour and its player total
    assert "19:00 UTC" in body, "peak hour"
    assert "84" in body, "the peak hour's player total (44 + 40)"
    # theatres, missions, modules
    assert "Syria" in body and "242 h" in body
    assert "Through the Inferno" in body and "214 h" in body
    assert "F-16C_50" in body and "268 h" in body
    # the per-server table
    for text in ("SRS-1", "TRAINING", "384 h", "312 h", "Caucasus", "Nevada", "31", "24"):
        assert text in body, f"per-server table: {text}"


@pytest.mark.asyncio
async def test_the_server_table_carries_new_players_between_unique_players_and_missions(
        app, monkeypatch):
    """The comparison grid has five data columns; new players follows unique players and each row
    prints that row's own figure, so a cell reading another field cannot pass."""
    data = payload()
    stub_read(monkeypatch, data)
    body = (await fetch(app)).text

    grid = body.split('class="stt-table"', 1)[1].split('<p class="stt-note"', 1)[0]
    headers = re.findall(r'<span role="columnheader"[^>]*>(.*?)</span>', grid, re.S)
    headers = [" ".join(re.sub(r"<[^>]+>", " ", span).split()) for span in headers]
    assert headers == [str(page.COL_SERVER), str(page.COL_PLAYTIME), str(page.COL_UNIQUE),
                       str(page.KPI_NEW), str(page.COL_MISSIONS), str(page.COL_THEATRE)], headers

    rows = [[" ".join(re.sub(r"<[^>]+>", " ", cell).split())
             for cell in re.findall(r'<span role="cell"[^>]*>(.*?)(?=<span role="cell"|</div>)',
                                    row, re.S)]
            for row in grid.split('<div class="stt-tr" role="row">')[1:]]
    assert len(rows) == len(data["usage"]), rows
    assert [len(row) for row in rows] == [len(headers)] * len(rows), rows
    assert [row[3] for row in rows] == [str(entry["new_players"]) for entry in data["usage"]], rows
    assert rows[0][3] != rows[1][3], "the two servers share a new-player figure: the fixture is blind"
    assert rows[0][3] != rows[0][2], "the new-player cell read the unique-player column"


@pytest.mark.asyncio
async def test_the_period_chips_map_to_the_periods_and_reach_the_read(app, monkeypatch):
    """The nine chips are the bot's own period vocabulary, and the read gets the filter's name plus
    the twelve-week window the three windowed cards state."""
    calls = stub_read(monkeypatch, payload())
    await fetch(app)
    assert calls[-1]["action"] == "get_server_statistics"
    assert calls[-1]["params"] == {"period": "month", "server": None,
                                   "window_days": page.RETENTION_DAYS}

    names = [name for name, _label in page.CHIPS]
    assert names == ["all", "today", "yesterday", "day", "week", "month", "quarter", "halfyear",
                     "year"], names
    for name in names:
        await fetch(app, f"{page.STATISTICS_PATH}?{page.PERIOD_PARAM}={name}")
        assert calls[-1]["params"]["period"] == name, f"chip {name} asked for a different period"

    # an unknown period is not passed through: the action's own fallback would be `all`
    await fetch(app, f"{page.STATISTICS_PATH}?{page.PERIOD_PARAM}=nonsense")
    assert calls[-1]["params"]["period"] == page.DEFAULT_PERIOD


@pytest.mark.asyncio
async def test_a_weekly_series_is_not_labelled_per_day(app, monkeypatch):
    """The bucket comes from the read: a week-bucketed series wears the week card's own words."""
    stub_read(monkeypatch, payload(unique_users_grain="week"))
    body = (await fetch(app)).text
    assert str(page.CARD_DAILY_WEEK) in body
    assert str(page.CARD_DAILY) not in body, "a weekly series was labelled per day"

    stub_read(monkeypatch, payload())
    body = (await fetch(app)).text
    assert str(page.CARD_DAILY) in body
    assert str(page.CARD_DAILY_WEEK) not in body


@pytest.mark.asyncio
async def test_the_scope_is_a_link_that_reaches_the_read_and_survives_the_chips(app, monkeypatch):
    """The scope selector lists the installation plus every server in the caller's view, and a
    scoped request reaches the action as a server name while the period chip stays put."""
    calls = stub_read(monkeypatch, payload())
    body = (await fetch(app)).text

    assert str(page.SCOPE_ALL) in body
    for name in HOSTED:
        assert name in body, f"the scope selector does not offer {name}"

    scoped = await fetch(app, f"{page.STATISTICS_PATH}?{page.SERVER_PARAM}=SRS-1"
                              f"&{page.PERIOD_PARAM}=quarter")
    assert scoped.status_code == 200
    assert calls[-1]["params"]["server"] == "SRS-1"
    assert calls[-1]["params"]["period"] == "quarter"
    # the scope stays put when another period is chosen
    assert 'href="/statistics?period="' not in scoped.text
    assert "period=week&amp;server=SRS-1" in scoped.text or "period=week&server=SRS-1" in scoped.text


# ------------------------------------------------------------------------ the empty state

@pytest.mark.asyncio
async def test_an_empty_read_renders_one_sentence_and_no_figures(app, monkeypatch):
    """A period with no sessions — or a read that could not run — is a sentence, never a zero.

    The asset version stamp (``?v=<digits>``) is stripped first: it is a fingerprint of the static
    files, so its digits are unrelated to what the page printed and a bare substring search over the
    whole document would fail on whatever number a rebuild produced.
    """
    stub_read(monkeypatch, None, success=False)
    body = ASSET_STAMP.sub("?v=", (await fetch(app)).text)
    assert str(page.EMPTY_PAGE) in body
    assert "1,284" not in body and "187" not in body
    assert str(page.DISTINCT_NOTE) not in body, "the table's sentence printed without a table"

    stub_read(monkeypatch, payload(usage=[], theatres=[], missions=[], modules=[], unique_users=[],
                                   retention=[], day_time=[], frequency=[],
                                   cluster={"playtime": 0, "players_distinct": 0, "new_players": 0,
                                            "returning_rate": 0, "avg_session": 0}))
    body = (await fetch(app)).text
    assert str(page.EMPTY_PAGE) in body, "an all-zero payload is the empty state, not four zeroes"


# ------------------------------------------------------------------------ what the page never shows

@pytest.mark.asyncio
async def test_the_page_carries_no_live_figure_and_no_node_fact(app, monkeypatch):
    """Load and node health belong to the dashboard's Load tab: this page answers period questions
    only, so neither the module nor the rendered page names a live or node series."""
    source = MODULE.read_text("utf-8")
    for forbidden in ("get_node_statistics", "node_statistics", "requests_wait_ms",
                      "pool_available", "dcs_queue", "asyncio_queue"):
        assert forbidden not in source, f"the page reads a live/node series: {forbidden}"

    stub_read(monkeypatch, payload())
    body = (await fetch(app)).text
    for forbidden in ("Requests queued", "Pool available", "DCS queue", "requests queued",
                      "pool available", "dcs queue", "requests_wait_ms", "players online"):
        assert forbidden not in body.split("<main")[-1], (
            f"a live/node fact reached the page: {forbidden}")


# ------------------------------------------------------------------------ i18n

@pytest.mark.asyncio
async def test_the_german_render_translates_the_pages_own_strings(app, monkeypatch):
    """Nothing English leaks through: the nav label and the cards come from the German catalog."""
    stub_read(monkeypatch, payload())
    body = (await fetch(app, cookies={i18n.COOKIE_NAME: "de"})).text

    assert "Statistik" in body, "the nav label is not translated"
    assert "Spieler pro Tag" in body, "a card title is not translated"
    assert "Players per day" not in body, "the English card title leaked into the German render"
    assert "Eindeutige Spieler" in body, "a KPI label is not translated"


def test_the_template_carries_no_figure_of_its_own():
    """No literal figure in the template: every number arrives as a value.

    Tags, Jinja comments and the ``{{ }}``/``{% %}`` expressions are removed first, so a class name
    (``g-2``) or a loop is not mistaken for a typed figure — what remains is the visible text and the
    static attributes, which must hold none.
    """
    source = re.sub(r"\{#.*?#\}", "", TEMPLATE.read_text("utf-8"), flags=re.S)
    source = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", source, flags=re.S)
    source = re.sub(r"<[^>]*>", "", source)
    digits = re.findall(r"\d+", source)
    assert not digits, f"the template types a figure of its own: {digits}"


# ------------------------------------------------------------------------ the derivations, once

def test_the_derivations_are_computed_once_from_the_payload():
    """Average session, retention share, sessions per player and the peak hour each have ONE home."""
    data = payload()
    kpis = {kpi["label"]: kpi for kpi in page.kpi_tiles(data, None)}
    assert kpis[page.KPI_PLAYTIME]["value"] == "1,284"
    assert kpis[page.KPI_UNIQUE]["unit"] == "", "only playtime carries an hour unit"
    assert kpis[page.KPI_PLAYTIME]["note"]["params"]["value"] == "1:22"
    assert kpis[page.KPI_RETURNING]["value"] == "38 %"

    retention = page.retention_rows(data["retention"])
    assert [(row["label"], row["share"]) for row in retention] == [
        ("2026-W41", "42 %"), ("2026-W42", "45 %")]
    # a week with no new players has no share, rather than a "0 %" that reads as nobody returned
    assert page.retention_rows([{"week": "2026-W40", "new_users": 0, "retained_users": 0}]) == []

    frequency = page.frequency_rows(data["frequency"])
    assert [row["value"] for row in frequency] == ["3.9", "5.5"]
    assert [row["width"] for row in frequency] == [72.1, 100.0]

    day_time = page.day_time_rows(data["day_time"])
    assert day_time["peak"] == "19"
    assert [row["players"] for row in day_time["rows"]][9] == "12"
    assert [row["players"] for row in day_time["rows"]][19] == "84"
    assert len(day_time["rows"]) == 24, "the profile is 24 hours, empty ones included"

    daily = page.daily_chart(data["unique_users"])
    # the HEIGHT is the count, on ONE axis: the busier day is the taller column, and the tallest
    # column is the axis' own plot height (a bar scaled to its own total said nothing)
    plot = page.DAILY_HEIGHT - page.DAILY_TOP - page.DAILY_BOTTOM
    axis_top = float(daily["axis"][-1]["text"].replace(",", ""))
    assert axis_top > 78, "the axis tops out above the peak, so the tallest column is inside the frame"
    assert [float(column["returning_height"]) + float(column["new_height"])
            for column in daily["columns"]] == pytest.approx(
                [plot * 78 / axis_top, plot * 64 / axis_top], abs=0.2)
    # the total is printed above every column, and the axis is a round one
    assert [value["text"] for value in daily["totals"]] == ["78", "64"]
    assert [tick["text"] for tick in daily["axis"]] == ["0", "20", "40", "60", "80"]

    servers = page.server_rows(data["usage"])
    assert servers["summed"] == "133"
    assert [row["theatre"] for row in servers["rows"]] == ["Caucasus", "Nevada"]
    # a server with no theatre recorded renders the dash, never an empty cell
    assert page.server_rows([{"server_name": "X", "playtime": 1, "players": 1, "missions": 1,
                              "theatre": None}])["rows"][0]["theatre"] == page.NO_VALUE


def test_a_bucket_with_no_session_draws_a_zero_column_not_a_gap():
    """For a count, "nobody flew" is data: the bar is empty and the number above it is 0."""
    chart = page.daily_chart([{"date": "2026-10-30", "total_players": 12, "new_players": 2},
                              {"date": "2026-10-31", "total_players": 0, "new_players": 0}])
    empty = chart["columns"][1]
    assert float(empty["returning_height"]) == 0.0 and float(empty["new_height"]) == 0.0
    assert chart["totals"][1]["text"] == "0"


def test_the_axis_labels_shorten_to_the_grain_and_thin_to_fit():
    """The dates are THINNED, never turned: about a dozen across, whatever the period's length."""
    assert page._axis_label("2026-10-31") == "10-31"
    assert page._axis_label("2026-W41") == "W41"
    assert page._axis_label("nonsense") == "nonsense"

    width = page.DAILY_WIDTH - page.DAILY_LEFT - page.DAILY_RIGHT

    def plan(count):
        return page._label_plan(count, width)

    assert plan(12) == (1, 1), "a 12-week series labels every bar"
    # 30 days: a date every second bar (15 across), but a count over EVERY bar — a number is narrower
    assert plan(30) == (2, 1)
    # 90 days: dates every sixth bar, counts every third
    assert plan(90) == (6, 3)

    def labels(count):
        return [{"date": f"2026-10-{index % 28 + 1:02d}"} for index in range(count)]

    ninety = page.daily_chart([dict(row, total_players=5, new_players=1) for row in labels(90)])
    assert len(ninety["labels"]) == 15, "the 90-day axis still prints ~30 dates"
    assert 28 <= len(ninety["totals"]) <= 32, "the numbers above the bars thinned too far"
    assert not any(label.get("rotate") for label in ninety["labels"]), "a diagonal survived"
