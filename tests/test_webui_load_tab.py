"""The dashboard's LOAD tab: the live window, node health and the per-scope series.

One section per acceptance criterion, driven through the REAL ASGI app where the criterion is about
what a request answers (the tab strip, the route, the scope bound, the live fragment) and through the
page module's own builders where the criterion is about a value (the composed series, the tick, the
dashes).

THE READ IS STUBBED AT THE PAGE'S SEAM (``dashboard.read_action``): the SQL and the bucket table
belong to the read action, so what this file pins is the TAB — which scope it asks for, how it
composes what comes back, and how often.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import pytest

from core.action_results import ActionResult
from conftest import asgi_client, create_app, install_shell, stub_identity
from services.webservice import readmodels
from services.webservice.readmodels import views as view_layer
from services.webservice.pages import dashboard as page
from services.webservice.scope import Scope
from test_webui_write_pause import Cluster, instance, server

REPO = Path(__file__).resolve().parent.parent
PANEL = REPO / "services" / "webservice" / "templates" / "_load_panel.html"
SHELL_JS = REPO / "services" / "webservice" / "static" / "shell.js"

HOSTED = ("SRS-1", "TRAINING", "PG-PvP")


def cluster() -> Cluster:
    """Two nodes and an offline third; the master carries two servers, the agent one."""
    one = server("SRS-1", ["Host A"], node_name="dcs-master-01", instance="Host-A-Instance")
    two = server("TRAINING", ["Host B"], node_name="dcs-master-01", instance="Train-Instance")
    three = server("PG-PvP", ["Host B"], node_name="dcs-a-01", instance="Pvp-Instance")
    master = types.SimpleNamespace(name="dcs-master-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", one)})
    agent = types.SimpleNamespace(name="dcs-a-01", master=False,
                                  instances={"Pvp-Instance": instance("Pvp-Instance", three)})
    return Cluster([one, two, three], nodes={"dcs-master-01": master, "dcs-a-01": agent,
                                             "dcs-offline": None},
                   node=master, instances=[instance("Host-A-Instance", one),
                                           instance("Train-Instance", two),
                                           instance("Pvp-Instance", three)])


@pytest.fixture
def app():
    application = create_app()
    install_shell(application, node=types.SimpleNamespace(master=True), config={})
    application.state.webui_source_provider = cluster().source
    return application


@pytest.fixture(autouse=True)
def clear_load_cache():
    """The load payload cache is process state: a payload cached by one test must not answer the next."""
    page.reset_load_cache()
    yield
    page.reset_load_cache()


# ------------------------------------------------------------------------------- the fixture payload

def series(values: tuple[float | None, ...], *, field: str = "players") -> list[dict]:
    """A load series of ``len(values)`` five-minute buckets, one gauge carrying *values*."""
    rows = []
    for index, value in enumerate(values):
        row = {"time": f"2026-10-31T{index // 12:02d}:{(index % 12) * 5:02d}:00",
               "players": 0, "cpu": 10.0, "mem_ram": 100.0, "mem_paged": 5.0, "read": 2.0,
               "write": 1.0, "sent": 2048.0, "recv": 1024.0, "fps": 60.0, "ping": 40.0}
        row[field] = value
        rows.append(row)
    return rows


def payload(rows: list[dict], *, scale: str = "24h", nodes=None) -> dict:
    return {
        "scale": scale,
        "window": {"start": "2026-10-30T00:00:00", "end": "2026-10-31T21:50:00",
                   "bucket_seconds": 300, "points": len(rows)},
        "series": rows,
        "nodes": nodes if nodes is not None else [
            {"node": "dcs-master-01", "pool_available": 17, "requests_queued": 4,
             "requests_wait_ms": 61, "dcs_queue": 6, "asyncio_queue": 3,
             "spark": [1, 2, 3, 2, 1]},
            {"node": "dcs-a-01", "pool_available": 16, "requests_queued": 2,
             "requests_wait_ms": 28, "dcs_queue": 3, "asyncio_queue": 1,
             "spark": [2, 2, 1, 2, 2]},
            {"node": "dcs-offline", "pool_available": None, "requests_queued": None,
             "requests_wait_ms": None, "dcs_queue": None, "asyncio_queue": None, "spark": []},
        ],
    }


def stub_read(monkeypatch, data=None, *, success=True, calls=None):
    """Replace the page's action seam, recording every call's parameters."""
    calls = calls if calls is not None else []

    async def _read(qualname, ctx, /, **params):
        calls.append({"action": qualname, "params": params})
        answer = data(params) if callable(data) else data
        return ActionResult(success=success, data=answer)

    monkeypatch.setattr(page, "read_action", _read)
    return calls


async def fetch(application, path="/", *, roles=frozenset({"Admin"}), scope=None):
    stub_identity(roles, scope=scope)
    async with asgi_client(application) as client:
        return await client.get(path)


# ------------------------------------------------------------------- the tab strip (no badge, no dot)

def test_the_load_tab_is_declared_as_a_tab_that_is_not_a_table():
    """The strip's keys are the read models' table names PLUS the one exception, declared here."""
    assert page.LOAD_TAB in page.TABS
    assert page.LOAD_TAB not in view_layer.TABLE_COLUMNS
    assert page.TABS[-1] == page.LOAD_TAB, "the design puts Load last"
    assert page.tab_capability(page.LOAD_TAB) == page.DASHBOARD_CAPABILITY
    assert page.LOAD_TAB in page.allowed_tabs({"DCS Admin"})
    assert page.LOAD_TAB not in page.allowed_tabs({"DCS"})


@pytest.mark.asyncio
async def test_the_tab_appears_in_the_strip_with_no_badge_and_no_dot(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text

    strip = body[body.index('<nav class="tabs"'):body.index("</nav>", body.index('<nav class="tabs"'))]
    load_tab = [chunk for chunk in strip.split("<a ")[1:] if f"tab={page.LOAD_TAB}" in chunk]
    assert load_tab, f"the Load tab is not in the strip: {strip}"
    assert "badge" not in load_tab[0], "the Load tab carries a count badge"
    assert "pdot" not in load_tab[0], "the Load tab carries a problem dot"
    assert str(page.LOAD_LABEL) in load_tab[0]


@pytest.mark.asyncio
async def test_the_four_table_tabs_still_carry_their_badges(app, monkeypatch):
    """POSITIVE CONTROL for the pin above: the other tabs still render their count."""
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app)).text
    for label in ("Servers", "Nodes", "Instances", "Players"):
        assert re.search(rf'{label} <span class="badge[^>]*>\d+</span>', body), label


# ------------------------------------------------------------------- the route renders the panel

@pytest.mark.asyncio
async def test_the_route_renders_the_chart_and_the_node_table(app, monkeypatch):
    stub_read(monkeypatch, payload(series((3, 8, 5))))
    response = await fetch(app, f"/?tab={page.LOAD_TAB}")

    assert response.status_code == 200
    body = response.text
    assert 'class="ld-svg"' in body, "no chart"
    assert 'role="table"' in body, "no node table"
    assert "dcs-master-01" in body and "dcs-a-01" in body
    assert body.count("<polyline") >= 1, "the players line is missing"
    assert 'stroke="' in body or 'class="ld-cpu"' in body, "the CPU series is missing"
    assert 'class="ld-now"' in body, "the now edge mark is missing"
    for value in ("17", "61 ms", "6", "3"):
        assert value in body, f"the node table lost {value}"


@pytest.mark.asyncio
async def test_the_scope_selector_offers_cluster_the_nodes_and_the_servers(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text

    select = body[body.index("<select"):body.index("</select>")]
    assert f'value="{page.LOAD_SCOPE_CLUSTER}"' in select, "no cluster scope"
    assert str(page.SCOPE_CLUSTER) in select
    assert f'value="{page.LOAD_SCOPE_NODE}dcs-master-01"' in select, "no node scope"
    assert f'value="{page.LOAD_SCOPE_NODE}dcs-a-01"' in select
    for name in HOSTED:
        assert f'value="{page.LOAD_SCOPE_SERVER}{name}"' in select, f"no server scope for {name}"
    assert str(page.SCOPE_NODE_GROUP) in select and str(page.SCOPE_SERVER_GROUP) in select


@pytest.mark.asyncio
async def test_the_five_scale_chips_and_the_live_pill_are_rendered(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text

    assert [name for name, _label in page.LOAD_SCALES] == ["1h", "6h", "24h", "7d", "30d"]
    for name, _label in page.LOAD_SCALES:
        assert f"scale={name}" in body, f"chip {name} is missing"
    assert "a sample every minute" in body
    assert "window ends 21:50" in body


@pytest.mark.asyncio
async def test_an_unknown_scale_falls_back_to_the_default(app, monkeypatch):
    calls = stub_read(monkeypatch, payload(series((1, 2, 3))))
    await fetch(app, f"/?tab={page.LOAD_TAB}&scale=nonsense")
    assert calls[-1]["action"] == "get_load_statistics"
    assert calls[-1]["params"]["scale"] == page.LOAD_DEFAULT_SCALE


# ------------------------------------------------------------------- a node scope sums its servers

@pytest.mark.asyncio
async def test_a_node_scope_asks_for_the_node_and_draws_the_sum_of_its_servers(app, monkeypatch):
    """A node's series is the SUM of its servers' rows — never one server's own series.

    The stub answers the way the read does: one series PER SERVER when a server is named, and the
    summed series when a node is. The page must ask for the NODE (not pick a server), and the chart
    it draws must be the summed one — so the peak printed is the two servers' peaks added up, which
    equals neither server's own peak.
    """
    one = [4, 6, 2]
    two = [3, 5, 1]
    summed = [a + b for a, b in zip(one, two)]

    def read(params):
        if params.get("server") == "SRS-1":
            return payload(series(tuple(one)))
        if params.get("server") == "TRAINING":
            return payload(series(tuple(two)))
        return payload(series(tuple(summed)))

    calls = stub_read(monkeypatch, read)
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}&scope={page.LOAD_SCOPE_NODE}dcs-master-01")).text

    assert calls[-1]["params"] == {"scale": "24h", "node": "dcs-master-01", "server": None}, \
        calls[-1]
    assert "peak 11 in this window" in body, "the node's peak is not its servers' sum"
    assert "peak 6 in this window" not in body, "the chart drew ONE server's series"

    # and the geometry follows the summed values, not either part's
    view = page.load_view(payload(series(tuple(summed))), scale="24h",
                          scope={"token": f"{page.LOAD_SCOPE_NODE}dcs-master-01", "kind": "node",
                                 "node": "dcs-master-01", "server": None},
                          groups=[], masters=set())
    peak_y = min(float(point.split(",")[1]) for point in view["chart"]["players"][0].split())
    axis = (page.LOAD_CHART_BOTTOM - page.LOAD_CHART_TOP) * 11 / 20
    assert peak_y == pytest.approx(page.LOAD_CHART_BOTTOM - axis), "the peak is not drawn at 11"

    own = page.load_view(payload(series(tuple(one))), scale="24h",
                         scope={"token": "cluster", "kind": "cluster", "node": None,
                                "server": None}, groups=[], masters=set())
    assert own["kpis"][0]["note"]["params"]["peak"] == "6", "the fixture is blind"


# ------------------------------------------------------------------- the 60 s tick is the only cadence

def test_the_load_tick_is_sixty_seconds():
    assert page.LOAD_REFRESH_SECONDS == 60, "the bot writes one row per server per MINUTE"


@pytest.mark.asyncio
async def test_two_renders_inside_the_minute_read_once_and_the_next_minute_reads_again(
        app, monkeypatch):
    """The stream ticks every couple of seconds; the LOAD read must not follow it."""
    calls = stub_read(monkeypatch, payload(series((1, 2, 3))))

    await fetch(app, f"/?tab={page.LOAD_TAB}")
    assert len(calls) == 1
    await fetch(app, f"/?tab={page.LOAD_TAB}")
    assert len(calls) == 1, "a second render inside the tick re-read the series"

    clock = [1000.0]
    monkeypatch.setattr(page.time, "monotonic", lambda: clock[0])
    page.reset_load_cache()
    await fetch(app, f"/?tab={page.LOAD_TAB}")
    assert len(calls) == 2
    clock[0] += page.LOAD_REFRESH_SECONDS - 1
    await fetch(app, f"/?tab={page.LOAD_TAB}")
    assert len(calls) == 2, "the read ran before the minute was up"
    clock[0] += 1
    await fetch(app, f"/?tab={page.LOAD_TAB}")
    assert len(calls) == 3, "the read did not run when the minute was up"


@pytest.mark.asyncio
async def test_the_chart_is_one_more_target_on_the_live_fragment_path(app, monkeypatch):
    """The same partial the page renders is the fragment the stream swaps, and it is stable inside
    the tick (so the stream's hash comparison sends nothing between two samples)."""
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    stub_identity({"Admin"})
    async with asgi_client(app) as client:
        first = await client.get(f"/api/dashboard/fragments?tab={page.LOAD_TAB}")
        second = await client.get(f"/api/dashboard/fragments?tab={page.LOAD_TAB}")

    assert first.status_code == second.status_code == 200
    fragments = first.json()
    assert "load-panel" in fragments, fragments.keys()
    assert fragments["load-panel"] == second.json()["load-panel"], \
        "the chart fragment changed inside one tick — the stream would send it every 2 s"

    other = None
    stub_identity({"Admin"})
    async with asgi_client(app) as client:
        other = await client.get("/api/dashboard/fragments")
    assert "load-panel" not in other.json(), "the chart is rendered on a tab that does not show it"


# ------------------------------------------------------------------- an offline node shows dashes

@pytest.mark.asyncio
async def test_an_offline_node_renders_dashes_and_never_zeroes(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text

    start = body.index("ld-row-off")
    row = body[start:body.index("</article>", start)]
    assert str(page.STATE_OFFLINE) in row, row
    cells = re.findall(r'<span role="cell" class="num">(.*?)</span>', row)
    assert cells and all(cell.strip() == page.NO_VALUE for cell in cells), cells
    assert ">0<" not in row, "an offline node was reported as zero"


def test_a_node_with_no_rows_is_offline_even_when_the_window_has_data():
    """The row's state is the DATA's, not the cluster's: all-``None`` figures say offline."""
    rows = page.load_node_rows([{"node": "dcs-offline", "pool_available": None,
                                 "requests_queued": None, "requests_wait_ms": None,
                                 "dcs_queue": None, "asyncio_queue": None, "spark": []}],
                               {"kind": "cluster"})
    assert rows[0]["online"] is False
    assert rows[0]["state"] == page.STATE_OFFLINE
    assert rows[0]["pool"] == page.NO_VALUE and rows[0]["wait"] == page.NO_VALUE


# ------------------------------------------------------------------- the scope bound, fail closed

@pytest.mark.asyncio
async def test_a_restricted_caller_is_offered_only_the_servers_they_hold(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}", roles=frozenset({"DCS Admin"}),
                        scope=Scope.restricted({"Host A"}))).text

    select = body[body.index("<select"):body.index("</select>")]
    assert f'value="{page.LOAD_SCOPE_SERVER}SRS-1"' in select
    assert "TRAINING" not in select and "PG-PvP" not in select, select
    assert f'value="{page.LOAD_SCOPE_CLUSTER}"' not in select, "a restricted caller was offered the cluster"
    assert f'value="{page.LOAD_SCOPE_NODE}' not in select, "a restricted caller was offered a node"


@pytest.mark.asyncio
async def test_a_scope_outside_the_callers_view_is_refused(app, monkeypatch):
    calls = stub_read(monkeypatch, payload(series((1, 2, 3))))
    restricted = Scope.restricted({"Host A"})
    for target in (f"{page.LOAD_SCOPE_NODE}dcs-a-01", f"{page.LOAD_SCOPE_SERVER}PG-PvP",
                   page.LOAD_SCOPE_CLUSTER):
        response = await fetch(app, f"/?tab={page.LOAD_TAB}&scope={target}",
                               roles=frozenset({"DCS Admin"}), scope=restricted)
        assert response.status_code == 403, f"{target} reached the read"
    assert not calls, "a refused scope still read the series"

    allowed = await fetch(app, f"/?tab={page.LOAD_TAB}&scope={page.LOAD_SCOPE_SERVER}SRS-1",
                          roles=frozenset({"DCS Admin"}), scope=restricted)
    assert allowed.status_code == 200
    assert calls[-1]["params"]["server"] == "SRS-1"
    assert calls[-1]["params"]["node"] is None


@pytest.mark.asyncio
async def test_an_unknown_node_name_is_refused_for_an_admin_too(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    response = await fetch(app, f"/?tab={page.LOAD_TAB}&scope={page.LOAD_SCOPE_NODE}ghost")
    assert response.status_code == 403


# ------------------------------------------------------------------- ids and the empty state

def test_the_svg_ids_are_stable_and_derived_from_the_key():
    """``hash()`` is salted per process: an id built from it breaks between two renders."""
    first = page._svg_id("node:dcs-a-01|24h", "load")
    second = page._svg_id("node:dcs-a-01|24h", "load")
    assert first == second and first.startswith("ld") and len(first) == 10
    assert first != page._svg_id("cluster|24h", "load")


@pytest.mark.asyncio
async def test_a_failed_read_renders_one_sentence_and_no_chart(app, monkeypatch):
    stub_read(monkeypatch, None, success=False)
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text
    assert str(page.LOAD_EMPTY) in body
    assert 'class="ld-svg"' not in body


# ------------------------------------------------------------------- the parked tab's stream

def test_the_client_closes_its_stream_when_the_tab_is_hidden(tmp_path):
    """A hidden browser tab must not keep an EventSource (and its minute of fragment polls) open."""
    source = SHELL_JS.read_text(encoding="utf-8")
    assert 'document.addEventListener("visibilitychange"' in source
    assert '"load-panel"' in source, "the chart is not a swap target"

    harness = tmp_path / "visibility_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    from conftest import run_node_harness
    done = run_node_harness(["node", str(harness), str(SHELL_JS)])
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["openedOnLoad"] == 1
    assert report["closedOnHidden"] is True, report
    assert report["openWhileHidden"] == 0, report
    assert report["createdAfterVisible"] == 2, "the return did not reopen the stream"
    assert report["openAfterVisible"] == 1


HARNESS = r"""
"use strict";
const fs = require("fs");
const SCRIPT = process.argv[2];
const STATUS = { id: "live-status", textContent: "", parentNode: { insertBefore() {} } };
const documentListeners = {};
const timers = {};
let nextTimer = 1;
const created = [];
function FakeEventSource(url) {
  this.url = url; this.closed = false; this.listeners = {};
  created.push(this);
  this.addEventListener = (kind, fn) => { (this.listeners[kind] = this.listeners[kind] || []).push(fn); };
  this.close = () => { this.closed = true; };
}
const scriptEl = { getAttribute: (name) => (name === "data-stream" ? "/api/dashboard/stream?tab=load"
  : name === "data-fragments" ? "/api/dashboard/fragments?tab=load" : null) };
const document = {
  currentScript: scriptEl, hidden: false, visibilityState: "visible",
  getElementById: (id) => (id === "live-status" ? STATUS : null),
  createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {},
                          textContent: "", id: "" }),
  addEventListener: (kind, fn) => { (documentListeners[kind] = documentListeners[kind] || []).push(fn); },
  body: { appendChild() {} },
};
const window = {
  location: { pathname: "/", assign() {} },
  EventSource: FakeEventSource,
  setInterval: (fn, ms) => { const id = nextTimer++; timers[id] = { fn, ms }; return id; },
  clearInterval: (id) => { delete timers[id]; },
  setTimeout: (fn) => { fn(); return 0; },
  clearTimeout: () => {},
  addEventListener: () => {},
};
const messages = process.env.DCSSB_MESSAGES_JS;
if (messages) { new Function("document", "window", fs.readFileSync(messages, "utf8"))(document, window); }
if (typeof window.dcssbMsg !== "function") { window.dcssbMsg = (key) => key; }
globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
globalThis.EventSource = FakeEventSource;
globalThis.window = window; globalThis.document = document;
globalThis.setInterval = window.setInterval; globalThis.clearInterval = window.clearInterval;
globalThis.setTimeout = window.setTimeout; globalThis.clearTimeout = window.clearTimeout;
new Function("document", "window", fs.readFileSync(SCRIPT, "utf8"))(document, window);
function open() { return created.filter((es) => !es.closed).length; }
function fire() { (documentListeners["visibilitychange"] || []).forEach((fn) => fn({})); }
const report = { openedOnLoad: created.length, openAfterLoad: open() };
document.hidden = true; document.visibilityState = "hidden";
fire();
report.closedOnHidden = created[0].closed;
report.openWhileHidden = open();
report.timersWhileHidden = Object.keys(timers).length;
document.hidden = false; document.visibilityState = "visible";
fire();
report.createdAfterVisible = created.length;
report.openAfterVisible = open();
process.stdout.write(JSON.stringify(report));
"""


# --------------------------------------------------------- the selector applies, the cards pick

@pytest.mark.asyncio
async def test_the_scope_selector_applies_on_change_without_a_submit_button(app, monkeypatch):
    """The enhancement is a delegated listener, so it outlives the live stream's fragment swap.

    The submit button is gone from the normal page (it did nothing a change did not already do) and
    survives only inside `<noscript>`, where it is the only way to apply the choice.
    """
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text

    assert "/static/load-scope.js" in body, "the change handler is not loaded"
    form = body[body.index('class="ld-scope"'):body.index("</form>")]
    assert f'name="{page.LOAD_SCOPE_PARAM}"' in form
    assert form.count('type="submit"') == 1, "no fallback left for a browser without the script"
    assert "<noscript>" in form and form.index("<noscript>") < form.index('type="submit"')

    script = (REPO / "services" / "webservice" / "static" / "load-scope.js").read_text("utf-8")
    assert "addEventListener('change'" in script, "the listener is not delegated"
    assert "requestSubmit" in script


@pytest.mark.asyncio
async def test_no_other_tab_loads_the_load_script(app, monkeypatch):
    """POSITIVE CONTROL: the script belongs to the tab that owns the control."""
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app)).text
    assert "/static/load-scope.js" not in body


def test_the_big_chart_draws_the_picked_pair_on_one_scale():
    """The mini cards' pairs are one quantity each, so they take ONE axis, never a percentage one."""
    rows = series((5, 5))
    rows[0]["read"], rows[0]["write"] = 4.0, 2.0
    data = payload(rows)
    scope = {"token": "", "kind": "cluster", "node": None, "server": None}

    players = page.load_view(data, scale="24h", scope=scope, groups=[], masters=set())
    disk = page.load_view(data, scale="24h", scope=scope, groups=[], masters=set(), graph="disk")

    assert players["legend"]["on"] is True
    assert players["chart"]["axis"]["right"], "the players graph lost its percentage axis"

    assert disk["card"]["title"] == page.MINI_DISK
    assert disk["card"]["series"] == page.MINI_DISK_META
    assert disk["legend"]["on"] is False, "a one-scale graph still drew the players/CPU legend"
    assert disk["chart"]["axis"]["right"] == [], "a one-scale graph drew a second axis"
    assert disk["chart"]["axis"]["left"][-1]["text"] == "5", "the axis is not the pair's own maximum"
    assert disk["chart"]["players"] != players["chart"]["players"], "the players line was redrawn"


@pytest.mark.asyncio
async def test_picking_a_card_swaps_the_graphs_instead_of_hiding_one(app, monkeypatch):
    """The pair leaving the big chart lands in the card just picked: the five graphs stay reachable."""
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}&{page.LOAD_GRAPH_PARAM}=memory")).text

    cards = re.findall(r'class="ld-card ld-pick" href="([^"]+)"', body)
    assert len(cards) == 4, cards
    # the memory slot now offers the default pair back, and no card offers the shown graph
    assert not any("graph=memory" in href for href in cards), "the big chart's graph is in a card too"
    back = [href for href in cards if f"{page.LOAD_GRAPH_PARAM}=" not in href]
    assert len(back) == 1, f"no card puts the default pair back: {cards}"

    form = body[body.index('class="ld-scope"'):body.index("</form>")]
    assert f'name="{page.LOAD_GRAPH_PARAM}" value="memory"' in form, (
        "picking a scope would drop the graph")


@pytest.mark.asyncio
async def test_a_graph_the_page_does_not_draw_falls_back_to_players(app, monkeypatch):
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab={page.LOAD_TAB}&{page.LOAD_GRAPH_PARAM}=nonsense")).text
    assert f'name="{page.LOAD_GRAPH_PARAM}" value="players"' in body


# --------------------------------------- the stream re-renders the panel the page actually drew

@pytest.mark.asyncio
async def test_the_live_url_carries_the_load_state_or_the_panel_reverts(app, monkeypatch):
    """The stream fetches its fragments from a SEPARATE request: a URL without the panel's own
    scale, scope and graph re-renders it at their defaults, and the reader's choice looks ignored.
    """
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab=load&{page.LOAD_SCALE_PARAM}=6h&{page.LOAD_GRAPH_PARAM}=disk")).text

    carried = re.search(r'data-fragments="([^"]+)"', body)
    assert carried, "the panel rendered without a live URL"
    query = carried.group(1)
    assert f"{page.LOAD_SCALE_PARAM}=6h" in query, query
    assert f"{page.LOAD_GRAPH_PARAM}=disk" in query, query
    assert f"{page.LOAD_SCOPE_PARAM}=" in query, query


@pytest.mark.asyncio
async def test_the_live_url_carries_a_node_scope(app, monkeypatch):
    """A node scope is the same trap: the stream would fall back to the cluster and redraw it."""
    stub_read(monkeypatch, payload(series((1, 2, 3))))
    body = (await fetch(app, f"/?tab=load&{page.LOAD_SCOPE_PARAM}=node:dcs-a-01")).text

    query = re.search(r'data-fragments="([^"]+)"', body).group(1)
    assert "scope=node%3Adcs-a-01" in query or "scope=node:dcs-a-01" in query, query


@pytest.mark.asyncio
async def test_the_stream_url_of_another_tab_carries_no_load_state(app, monkeypatch):
    """POSITIVE CONTROL: the state rides only when the Load tab rendered it."""
    body = (await fetch(app)).text
    query = re.search(r'data-fragments="([^"]+)"', body).group(1)
    assert page.LOAD_SCALE_PARAM not in query, query
    assert page.LOAD_GRAPH_PARAM not in query, query


@pytest.mark.asyncio
async def test_a_long_server_name_is_a_scope_this_page_accepts(app, monkeypatch):
    """The token is a NAME, not a sort column: the 32-character cap cut it and refused a server the
    selector itself had offered, which is a 403 for a scope the reader does hold.
    """
    from urllib.parse import quote

    long_name = "Special K's Persian Gulf Training Server"
    assert len(long_name) > 32, "the fixture no longer tests what it says"

    one = server("SRS-1", ["Host A"], node_name="dcs-master-01", instance="Host-A-Instance")
    two = server(long_name, ["Host B"], node_name="dcs-master-01", instance="Train-Instance")
    master = types.SimpleNamespace(name="dcs-master-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", one)})
    app.state.webui_source_provider = Cluster(
        [one, two], nodes={"dcs-master-01": master}, node=master,
        instances=[instance("Host-A-Instance", one), instance("Train-Instance", two)]).source

    stub_read(monkeypatch, payload(series((1, 2, 3))))
    token = quote(f"{page.LOAD_SCOPE_SERVER}{long_name}", safe="")
    response = await fetch(app, f"/?tab={page.LOAD_TAB}&{page.LOAD_SCOPE_PARAM}={token}")

    assert response.status_code == 200, "a server the selector offered was refused"
    # the resolved scope reaches the stream URL, so the panel is not silently the cluster's
    from urllib.parse import urlencode
    carried = urlencode({page.LOAD_SCOPE_PARAM: f"{page.LOAD_SCOPE_SERVER}{long_name}"})
    assert carried in response.text, "the scope was refused or dropped"


def test_the_scope_token_is_sized_for_a_name_and_not_for_a_sort_column():
    longest = max(len(name) for name in ("Special K's Persian Gulf Training Server",
                                         "A" * 64))
    assert page.MAX_SCOPE_TOKEN > longest + len(page.LOAD_SCOPE_SERVER)


def test_the_five_graphs_appear_once_across_the_chart_and_the_cards():
    """A swap, not a one-way trip: whichever pair is big, the other four are on the cards."""
    scope = {"token": "", "kind": "cluster", "node": None, "server": None}
    data = payload(series((1, 2, 3)))
    every = set(page.LOAD_GRAPHS)

    for graph in every:
        view = page.load_view(data, scale="24h", scope=scope, groups=[], masters=set(), graph=graph)
        shown = [card["name"] for card in view["minis"]]
        assert len(shown) == 4, shown
        assert set(shown) == every - {graph}, (graph, shown)
        if graph != page.LOAD_GRAPH_DEFAULT:
            assert shown[page.LOAD_MINI_SLOTS.index(graph)] == page.LOAD_GRAPH_DEFAULT, (
                "the pair that left the big chart did not land in the card that was picked")


@pytest.mark.asyncio
async def test_the_tab_is_not_offered_without_the_monitoring_plugin(app, monkeypatch):
    """The tab's read belongs to the optional monitoring plugin: no plugin, no tab — and asking for
    it by name renders the default tab rather than a panel nothing can feed."""
    monkeypatch.setattr(page, "action_registry", lambda: {})

    assert page.load_tab_available() is False
    assert page.LOAD_TAB not in page.allowed_tabs(frozenset({"Admin"}))

    body = (await fetch(app, f"/?tab={page.LOAD_TAB}")).text
    assert "ld-bar" not in body, "the panel rendered without the plugin that feeds it"
    assert not re.search(rf'>\s*{str(page.LOAD_LABEL)}\s*<', body), "the strip still offers the tab"
