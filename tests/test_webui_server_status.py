"""The server's status keeps updating on its own, on every tab of the server page (L5).


    *"When I startup a server and I stay on the DCS log tab, the server status at the top stays on
      'loading' and never goes to 'Paused' or 'running', when the server is up."*

WHAT WAS WRONG (the mechanism, established with evidence). The server page's status was a SERVER
RENDER and nothing refreshed it. The console's ONE live mechanism (``static/shell.js`` + ``pages/live``,
the dashboard's SSE fragments) is loaded ONLY by the dashboard and the Logs page: the server page
passes no ``live`` context, so ``base.html`` emits no live script there. On the log tabs the only
script that ran was the log follower (``static/server-log.js``), which owns the log card alone. So the
status moved ONLY on a reload — which a tab switch is — on EVERY tab, not just the log ones. The
follower was NOT interfering with a shared poll: there is no shared poll on this page, and the
follower's timer/backoff/visibility handling is self-contained (a separate IIFE on its own element).

THE FIX. The status gets its OWN poll — the same discipline the log follower uses — and the status
region is REGISTERED on the render (the page head on every tab, the Overview card's copy), fed by the
SAME read model (``readmodels.servers.server_view``) and rendered by the SAME partial, so one fact has
one updater and a rendered status cannot disagree with a polled one. It is deliberately NOT the log
script refreshing the status (that would be a second source for one fact).

The suite drives the app end to end (the region on every tab, the poll's authz and its current answer)
and the SHIPPED script through a browser-less harness (``server_status_harness.js``) — including the
card's headline case: the status moves while the LOG follower is also running.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core import Status
from services.webservice.pages import logs as logs_page
from services.webservice.pages import server_detail as detail_page

from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in,
)
from test_webui_server_log_tab import (  # noqa: F401 - fixtures/helpers imported on purpose
    MASTER, SERVER, admin_app, cluster, write_log, LINES,
)
from test_webui_write_server_actions import fleet as server_fleet  # noqa: F401

DASH_STATUS = f"/fleet/{SERVER}/status"
LOG_PAGE = f"/fleet/{SERVER}?tab=log"

TABS = ("overview", "players", "missions", "log", "configuration")


# ------------------------------------------------------------------------ 1. the region on the render

@pytest.mark.asyncio
@pytest.mark.parametrize("tab", TABS)
async def test_the_status_region_and_its_poll_url_are_on_every_tab(config_dir, tmp_path, clean_registry,
                                                                  tab):
    """The page head is OUTSIDE the tab branches, so the status region — and the poll's URL and cadence
    — are on every tab of the server page, not only on the log tabs. THIS is the registration the fix
    adds: before it, the page rendered a bare ``<span class="tag">`` with no marker to update."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(f"/fleet/{SERVER}?tab={tab}")).text
    assert 'data-server-status-url="/fleet/SRS-1/status"' in html, tab
    assert 'data-server-status-seconds="10"' in html, tab
    assert re.search(r'<span class="tag" data-server-status>', html), f"{tab}: no status region"
    assert "/static/server-status.js" in html, tab


@pytest.mark.asyncio
async def test_the_overview_card_carries_the_same_live_region(config_dir, tmp_path, clean_registry):
    """The Overview tab shows the status in TWO places (the page head, the card). Both are live regions
    so a reader on that tab cannot see one of them move and the other stand still."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(f"/fleet/{SERVER}?tab=overview")).text
    assert len(re.findall(r'<span class="tag" data-server-status>', html)) == 2


def test_the_status_route_is_registered_and_carries_the_pages_capability(config_dir, tmp_path,
                                                                        clean_registry):
    """No new capability: the poll is gated with the server page's own ``servers.view``, and it is a
    non-page route (it carries no nav item)."""
    app = admin_app(config_dir, tmp_path)
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    assert detail_page.SERVER_STATUS_PATH in routes
    assert detail_page.SERVER_STATUS_PATH not in registrar.page_paths
    assert registrar.capability_for(route=routes[detail_page.SERVER_STATUS_PATH]) == \
        detail_page.SERVER_DETAIL_CAPABILITY


# ------------------------------------------------------------------------------------ 2. the poll

@pytest.mark.asyncio
async def test_the_status_poll_answers_the_current_status(config_dir, tmp_path, clean_registry):
    """THE defect, at the route: the page is rendered while the server is LOADING, the server then comes
    up, and the poll answers the NEW status (and its dot) — no reload. Before the fix this route did
    not exist, which is why the mark never moved."""
    write_log(tmp_path, LINES)
    cl = cluster(tmp_path)
    srv = cl.servers[0]
    srv.status = Status.LOADING
    app = build(config_dir, cl, plugins=())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(LOG_PAGE)).text
        assert "LOADING" in html, "the page first rendered the LOADING mark"
        still_loading = (await http.get(DASH_STATUS)).json()
        srv.status = Status.RUNNING
        running = (await http.get(DASH_STATUS)).json()
    assert "LOADING" in still_loading["status"] and "pause" in still_loading["status"]
    assert "RUNNING" in running["status"], "the poll must answer the CURRENT status"
    assert "run" in running["status"], "the dot must move with the word"


@pytest.mark.asyncio
async def test_the_log_tab_page_carries_the_status_poll_beside_the_log(config_dir, tmp_path,
                                                                      clean_registry):
    """THE card's headline, at the app level: on the LOG tab the page carries BOTH the log card AND the
    status region with its poll URL, so the status can move while the operator watches the log — and a
    status change between the render and the poll is what the poll then answers."""
    write_log(tmp_path, LINES)
    cl = cluster(tmp_path)
    srv = cl.servers[0]
    srv.status = Status.LOADING
    app = build(config_dir, cl, plugins=())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(LOG_PAGE)).text
        assert 'id="dcs-log-card"' in html, "the log tab's card is there"
        assert 'data-server-status-url="/fleet/SRS-1/status"' in html, "and so is the status poll"
        srv.status = Status.PAUSED
        polled = (await http.get(DASH_STATUS)).json()
    assert "PAUSED" in polled["status"]


@pytest.mark.asyncio
async def test_the_status_route_is_a_get_and_never_a_post(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(DASH_STATUS)
    assert reply.status_code == 405


# ------------------------------------------------------------------------------------- 3. the gate

@pytest.mark.asyncio
async def test_an_anonymous_caller_is_refused(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        reply = await http.get(DASH_STATUS)
    assert reply.status_code == 403


@pytest.mark.asyncio
async def test_a_caller_who_manages_another_server_cannot_poll_this_one(config_dir, clean_registry):
    """The poll reads the SAME scoped source the page does: a manager of Host B cannot poll Host A's
    server — and the refusal discloses nothing (the same 403 a name that does not exist gets)."""
    app = build(config_dir, server_fleet(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "hoster")                     # scope: Host A
        mine = await http.get("/fleet/SRS-1/status")
        theirs = await http.get("/fleet/SRS-B/status")
        absent = await http.get("/fleet/ghost/status")
    assert mine.status_code == 200
    assert theirs.status_code == 403
    assert absent.status_code == 403, "an absent name and an out-of-scope one refuse identically"


@pytest.mark.asyncio
async def test_a_server_named_ending_in_status_still_opens_its_page(config_dir, clean_registry):
    """The collision guard: a server whose OWN name ends in ``/status`` shares this route's shape, so its
    page must still open rather than be swallowed by the poll."""
    from test_webui_write_pause import Cluster, instance, server
    import types
    odd = server("Ops/status", ["Host A"])
    node = types.SimpleNamespace(name="dcs-a-01", master=True,
                                 instances={"Host-A-Instance": instance("Host-A-Instance", odd)})
    cl = Cluster([odd], nodes={"dcs-a-01": node}, node=node,
                 instances=[instance("Host-A-Instance", odd)])
    app = build(config_dir, cl, plugins=())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get("/fleet/Ops/status")
        # the LONGER name is checked first, so this renders the server's PAGE (a full document)…
        assert page.status_code == 200
        assert "<!DOCTYPE html>" in page.text
        # …while a poll for the shorter server it shadows answers the status JSON, and the shorter name
        # does not exist here, so the page's own 404 is the honest answer.
        ghost = await http.get("/fleet/Ops/status/status")
    assert ghost.status_code in (200, 404)


# ------------------------------------------------- 4. the shipped script (browser-less harness)

TESTS = Path(__file__).resolve().parent
STATUS_JS = TESTS.parent / "services" / "webservice" / "static" / "server-status.js"
LOG_JS = TESTS.parent / "services" / "webservice" / "static" / "server-log.js"
HARNESS = TESTS / "server_status_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def poll() -> dict:
    """The shipped status poll's own answers, driven by ``server_status_harness.js`` BESIDE the shipped
    log follower — the two scripts on the real page."""
    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert STATUS_JS.is_file(), "the shipped server-status script is missing"
    assert LOG_JS.is_file(), "the shipped server-log script is missing"
    done = subprocess.run(["node", str(HARNESS), str(STATUS_JS), str(LOG_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@requires_node
def test_the_poll_keeps_the_status_current_on_its_own(poll):
    """One immediate poll on arrival, painted into EVERY region, then a tick paints the changed mark."""
    assert poll["boot"]["fetches"] == [f"/fleet/{SERVER}/status"]
    assert poll["boot"]["painted"] and poll["boot"]["paintedBoth"], "all regions are painted"
    assert poll["boot"]["timerCount"] == 1, "a timer is armed while the page is open"
    assert poll["tick"]["moved"] and poll["tick"]["cardMoved"], "the tick moves the mark, no reload"


@requires_node
def test_the_status_updates_while_the_log_tab_is_open(poll):
    """THE headline, over the shipped scripts: with the LOG follower running, the status poll runs too
    (both intervals armed, both endpoints asked) and a status change still lands on the mark — neither
    script stops the other."""
    case = poll["withLogTab"]
    assert case["bootTimers"] == 2, "the log follower and the status poll both run"
    assert case["logFollowed"] and case["statusPolled"]
    assert case["statusMoved"], "the status moves while the log tab is open"


@requires_node
def test_the_poll_STOPS_when_the_browser_tab_is_hidden(poll):
    """The console's rule: nothing polls in a background tab. The interval is cleared and NO request is
    made, and it resumes on its own when the tab is shown again."""
    hidden = poll["hidden"]
    assert hidden["timerCount"] == 0, "the interval must be cleared when the tab is hidden"
    assert hidden["requestsWhileHidden"] == 0, "no poll may be made while the tab is hidden"
    assert hidden["resumedTimers"] == 1 and hidden["requestOnResume"] is True


@requires_node
def test_a_failed_poll_backs_off_and_recovers(poll):
    """A failed poll drops the interval and arms ONE retry on a growing delay; firing it recovers."""
    backoff = poll["backoff"]
    assert backoff["timerCount"] == 0, "the normal interval is dropped while failing"
    assert backoff["timeoutCount"] == 1, "exactly one backoff retry is pending"
    assert backoff["retried"] and backoff["recovered"], "the retry polls again and paints the answer"
    assert backoff["timerCountAfterRetry"] == 1


@requires_node
def test_the_script_does_nothing_without_a_status_region(poll):
    """A page with no status mark (and no URL) is untouched: no timer, no request."""
    assert poll["noRegion"] == {"timers": 0, "fetched": 0}
