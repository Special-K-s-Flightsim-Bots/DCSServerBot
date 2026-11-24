"""Switching an extension ON is handed to a background job, and the switch says so.

THE PROBLEM. An enable runs the extension's own ``install()``/``prepare()``; for one that ships a
package (Olympus) that is a pip install of minutes. The switch POST awaited it, so the request stayed
open, the browser had nothing to show, and a second click — or a second operator — started a SECOND
install into the same venv.

WHAT IS PINNED HERE:
* the POST returns AT ONCE with the action still running, and the action runs on a background task;
* while it runs the card draws the switch in its ``busy`` state — no form, no POST, the job's key and
  the state endpoint on the element — so the page never offers a second press;
* a SECOND attempt for the same extension is REFUSED by the server with nothing started;
* a DIFFERENT extension is not blocked by it;
* the poll endpoint reports exactly the running jobs of that node;
* the finished job's own message becomes the page's notice, ONCE, and not again on a reload.

The bot is down in these fixtures, so the live action is stubbed: ``action_available`` says yes and
``call_action`` waits on an event the test controls. That keeps the guarantee honest — the POST must
answer while the action is STILL RUNNING, which is exactly what a slow install does.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import extension_switch
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _app  # noqa: F401  (fixtures node_dir / node_data)
from test_webui_node_page import node_data, node_dir  # noqa: F401
from test_webui_settings_node_scope import _csrf, _revision

NODES = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    Olympus:
      nodejs: C:/Program Files/nodejs
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        Olympus:
          enabled: true
          backend: {address: localhost, port: 4512}
          frontend:
            path: C:/Olympus
            port: 4513
            elevationProvider:
              provider: https://example.org/{lat}{lng}.hgt
        Trackfile:
          enabled: true
          target: /opt/track
"""

TAB = f"{node_settings_page.node_page_url('dcs-a-01')}?tab=1"
STATE_URL = node_settings_page.NODE_PAGE_SWITCH_STATE_PATH.format(node="dcs-a-01")


class _Result:
    def __init__(self, success: bool, message: str) -> None:
        self.success = success
        self.message = message


@pytest.fixture
def switch_dir(node_dir):
    (node_dir / "nodes.yaml").write_text(NODES, encoding="utf-8")
    extension_switch.reset(_app(node_dir).state)
    return node_dir


@pytest.fixture
def live_action(monkeypatch):
    """A bot that offers the action, whose call the test can hold open like a slow install."""
    gate = asyncio.Event()

    async def slow_call(qualname, ctx, **params):
        await gate.wait()
        return _Result(True, f"Extension '{params['extension']}' enabled on server 'Syria'.")

    monkeypatch.setattr(node_settings_page, "action_available", lambda qualname: True)
    monkeypatch.setattr(node_settings_page, "_instance_server_name",
                        lambda request, node_key, instance: "Syria")
    monkeypatch.setattr(node_settings_page, "call_action", slow_call)
    return gate


def _switch_form(token: str, revision: str, action: str) -> dict:
    return {
        "_csrf_token": token, "_revision": revision,
        node_settings_page.NODE_PAGE_SCOPE_FIELD: "instance:Syria",
        node_settings_page.NODE_PAGE_EXTENSION_FIELD: "Olympus",
        node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD: action,
    }


def test_a_second_attempt_is_refused_by_the_registry():
    """The lock is the registry's, not the page's: it answers a second claim, whoever posts it."""
    from starlette.datastructures import State

    state = State()
    first = extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "enable")
    assert first is not None and first.running
    assert extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "enable") is None
    # a DIFFERENT extension of the same instance is its own job
    other = extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Trackfile", "disable")
    assert other is not None
    extension_switch.finish(state, first, True, "done")
    assert extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus",
                                  "disable") is not None
    assert extension_switch.job_for(state, "dcs-a-01", "Syria", "Trackfile") is other


def test_a_finished_result_is_taken_once():
    from starlette.datastructures import State

    state = State()
    job = extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "enable")
    extension_switch.finish(state, job, False, "could not install")
    taken = extension_switch.take_results(state, "dcs-a-01")
    assert [entry.message for entry in taken] == ["could not install"]
    assert extension_switch.take_results(state, "dcs-a-01") == []


@pytest.mark.asyncio
async def test_the_switch_answers_while_the_install_runs_and_locks_itself(switch_dir, node_data,
                                                                         live_action):  # noqa: F811
    """The report's own flow: press the switch and be answered NOW, with the card showing it runs."""
    app = _app(switch_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(TAB)
        reply = await client.post(
            TAB, data=_switch_form(_csrf(page.text), _revision(page.text),
                                   node_settings_page.NODE_PAGE_EXTENSION_DISABLE))
        # ANSWERED, and the action has NOT finished — this is the whole point
        assert reply.status_code == 303, reply.text
        # and it returns to the SAME tab: the in-flight switch lives there, so the operator must land
        # on it rather than being thrown to the Node-wide tab
        assert reply.headers["location"].endswith("?tab=1"), reply.headers["location"]
        rendered = await client.get(TAB)
        assert extension_switch.job_for(app.state, "dcs-a-01", "Syria", "Olympus") is not None
        assert not live_action.is_set()
        again = await client.post(
            TAB, data=_switch_form(_csrf(rendered.text), _revision(rendered.text),
                                   node_settings_page.NODE_PAGE_EXTENSION_ENABLE))
        refused = await client.get(TAB)
        state = await client.get(STATE_URL)
        assert again.status_code == 303
        assert "data-switch-busy" in rendered.text, "the card did not draw the in-flight switch"
        assert STATE_URL in rendered.text, "the card did not name the endpoint it polls"
        assert "already being switched" in refused.text, "a second press was not refused"
        assert list(state.json()["running"].values()) == ["disable"]
        # the pending form is NOT offered while the job runs: no POST, no switch button
        assert 'name="_ext_action" value="enable"' not in rendered.text
        live_action.set()
        await asyncio.sleep(0.05)
        done = await client.get(TAB)
        after = await client.get(TAB)
    assert "enabled on server" in done.text, "the background result never reached the page"
    assert "enabled on server" not in after.text, "the result was shown twice"
    assert extension_switch.job_for(app.state, "dcs-a-01", "Syria", "Olympus") is None


@pytest.mark.asyncio
async def test_the_switch_task_is_held_so_it_cannot_be_collected(switch_dir, node_data,  # noqa: F811
                                                                 live_action):
    """A bare ``create_task`` keeps only a WEAK reference: the task can be collected at an await point,
    which cancels it silently — the card blinks forever and the action never ran. That was the observed
    defect. The registry must HOLD the task: on the job, and in the module's own set."""
    app = _app(switch_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(TAB)
        await client.post(TAB, data=_switch_form(_csrf(page.text), _revision(page.text),
                                                 node_settings_page.NODE_PAGE_EXTENSION_DISABLE))
        job = extension_switch.job_for(app.state, "dcs-a-01", "Syria", "Olympus")
        assert job is not None
        assert job.task is not None, "the job carries no task reference — it can be collected"
        assert job.task in extension_switch._TASKS, "the module does not hold the task either"
        assert not job.task.done()
        live_action.set()
        await asyncio.sleep(0.05)
    assert job.task.done(), "the task never ran to completion"
    assert job.task not in extension_switch._TASKS, "a finished task stayed in the hold set"


@pytest.mark.asyncio
async def test_a_cancelled_task_closes_the_job_instead_of_blinking_forever(switch_dir, node_data,  # noqa: F811
                                                                           live_action):
    """A shutdown (or a collected task) cancels the task. The job must still be closed — otherwise the
    card has no state to stop on and the operator watches "Disabling…" for as long as the page is open."""
    app = _app(switch_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(TAB)
        await client.post(TAB, data=_switch_form(_csrf(page.text), _revision(page.text),
                                                 node_settings_page.NODE_PAGE_EXTENSION_DISABLE))
        job = extension_switch.job_for(app.state, "dcs-a-01", "Syria", "Olympus")
        assert job is not None and job.task is not None
        job.task.cancel()
        await asyncio.sleep(0.05)
        assert not job.running, "the cancelled job is still 'running' — the card would blink forever"
        assert extension_switch.job_for(app.state, "dcs-a-01", "Syria", "Olympus") is None
        state = await client.get(STATE_URL)
        assert state.json()["running"] == {}
        page_after = await client.get(TAB)
    assert "stopped before it" in page_after.text, "the operator was never told the switch died"


@pytest.mark.asyncio
async def test_the_busy_card_polls_a_url_the_mounted_console_answers(switch_dir, node_data,  # noqa: F811
                                                                    live_action):
    """A console served under a prefix must poll the endpoint UNDER THAT PREFIX.

    While a public site owns ``/`` the console's whole page tree moves to ``/console``. Every URL a
    page builds goes through ``mount.url`` for exactly that reason; a poll path written raw names the
    SITE's address instead, so the request answers 404 ("not the console's page") for as long as the
    switch runs — the job finishes in the log while the card keeps pulsing, because nothing the page
    can reach ever reports it.
    """
    from services.webservice import mount

    app = _app(switch_dir)
    registrar = app.state.webui_registrar
    registrar.move_console(mount.CONSOLE_PREFIX)
    mount._PREFIX = mount.CONSOLE_PREFIX
    app.state.webui_console_prefix = mount.CONSOLE_PREFIX
    tab = f"{mount.CONSOLE_PREFIX}{TAB}"
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(tab)
        assert page.status_code == 200, page.text[:300]
        posted = await client.post(tab, data=_switch_form(_csrf(page.text), _revision(page.text),
                                                         node_settings_page.NODE_PAGE_EXTENSION_DISABLE))
        assert posted.status_code == 303, posted.text
        rendered = await client.get(tab)
        polled = re.findall(r'data-switch-state="([^"]*)"', rendered.text)
        assert polled, "the card drew no in-flight switch"
        answer = await client.get(polled[0])
        raw = await client.get(node_settings_page.NODE_PAGE_SWITCH_STATE_PATH.format(node="dcs-a-01"))
        live_action.set()
        await asyncio.sleep(0.05)
    assert polled[0].startswith(mount.CONSOLE_PREFIX + "/"), (
        f"the card polls {polled[0]!r} — a path the console does not answer under its mount")
    assert answer.status_code == 200, f"the card's own poll URL answered {answer.status_code}"
    assert list(answer.json()["running"].values()) == ["disable"]
    assert raw.status_code == 404, "the unprefixed path still answers — the mount is not in force"


@pytest.mark.asyncio
async def test_the_in_flight_script_states_the_asset_version(switch_dir, node_data):  # noqa: F811
    """The poll script is versioned in its URL: a browser holding yesterday's copy must FETCH the new
    one instead of running the old loop against a fixed endpoint.

    A cached copy is a fixed page: nothing in the render reaches it, so whether the card ever stops
    pulsing is decided by the script the browser already holds. The version is derived from the
    shipped directory, never re-declared.
    """
    from services.webservice import templating

    app = _app(switch_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(TAB)
        assert page.status_code == 200, page.text[:300]
        html = page.text
    newest = max(entry.stat().st_mtime_ns for entry in templating.SHELL_STATIC_DIR.iterdir()
                 if entry.is_file())
    match = re.search(r'<script src="/static/extension-switch\.js\?v=([^"]*)"', html)
    assert match, "the page loads the in-flight script with no asset version"
    assert match.group(1) == str(newest), "the in-flight script states a stale asset version"


@pytest.mark.asyncio
async def test_the_busy_row_is_the_label_alone_and_nothing_takes_its_room(switch_dir,  # noqa: F811
                                                                         node_data,
                                                                         live_action):
    """The in-flight foot holds the switch and its buttons — and NO clock eating the label.

    Reported from the console: on a narrow card the row read "E… 0:11" — the elapsed time had squeezed
    a translated label to one letter — and the foot overflowed the card, pushing the trashcan and
    Config past its edge. The clock is gone by design; the pulse alone says "still running", and the
    journal's line carries the seconds. So: the label must render WHOLE, and nothing may take its room.
    """
    app = _app(switch_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(TAB)
        posted = await client.post(TAB, data=_switch_form(_csrf(page.text), _revision(page.text),
                                                         node_settings_page.NODE_PAGE_EXTENSION_DISABLE))
        assert posted.status_code == 303, posted.text
        rendered = await client.get(TAB)
        html = rendered.text
        live_action.set()
        await asyncio.sleep(0.05)
    busy = re.search(r'<span class="st-sw busy".*?</span></span>', html, re.S)
    assert busy, "the card drew no in-flight switch"
    markup = busy.group(0)
    assert "data-switch-elapsed" not in markup and "st-busy-time" not in markup, (
        "the foot still draws a clock beside the label — that is what squeezed it to \"E…\"")
    assert '<span class="st-sw-label">' + str(node_settings_page.NODE_PAGE_EXTENSION_BUSY_DISABLE) \
        + '</span>' in markup, "the in-flight label is not rendered, whole, in its own element"
    static = Path(node_settings_page.__file__).parent.parent / "static"
    css = (static / "shell.css").read_text(encoding="utf-8")
    assert ".st-busy-time" not in css, "the sheet still reserves room for a clock the markup never draws"
    assert re.search(r"\.st-sw\.busy\s+\.st-sw-label\s*\{[^}]*text-overflow:\s*ellipsis", css), \
        "the in-flight label cannot give way — a long translation would push the buttons out"
    assert re.search(r"\.st-sw\.busy\s+\.st-sw-label\s*\{[^}]*min-width:\s*6ch", css), \
        "the in-flight label has no floor — on a tight card it collapses to a letter (\"E…\")"
    script = (static / "extension-switch.js").read_text(encoding="utf-8")
    assert "data-switch-elapsed" not in script, "the script still writes a clock into the row"
    assert "data-switch-state" in script, "the script no longer polls the job's state"


def test_a_disable_that_never_returns_stops_being_drawn_as_busy():
    """A disable runs shutdown hooks; if the action never returns, the page must stop pulsing and SAY
    so, instead of blinking for as long as the tab is open. The job keeps its claim (no second attempt
    beside a stuck one), it just stops being "in progress" on the page."""
    import time as _time

    from starlette.datastructures import State

    state = State()
    job = extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "disable")
    job.started = _time.time() - (extension_switch._UI_WAIT_SECONDS["disable"] + 5)

    class NeverEnds:
        def done(self) -> bool:
            return False

    job.task = NeverEnds()
    assert extension_switch.job_for(state, "dcs-a-01", "Syria", "Olympus") is job
    extension_switch.reconcile(state, "dcs-a-01")
    # the PAGE stops drawing it...
    assert extension_switch.job_for(state, "dcs-a-01", "Syria", "Olympus") is None
    assert extension_switch.running(state, "dcs-a-01") == []
    # ...and it says why, on the reload the page now makes
    taken = extension_switch.take_results(state, "dcs-a-01")
    assert [entry.ok for entry in taken] == [False]
    assert "has not finished after 3 minutes" in taken[0].message
    # THE LOCK SURVIVES: a stuck switch is not a free switch
    assert extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus",
                                  "disable") is None


def test_a_job_whose_task_died_is_reconciled_by_the_poll():
    """The poll endpoint is the page's only heartbeat: a job whose task is gone must stop being reported,
    even if nothing ever called ``finish`` — that is what turns a dead task into a visible outcome."""
    from starlette.datastructures import State

    state = State()
    job = extension_switch.begin(state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "disable")

    async def dies() -> None:
        return None

    async def run():
        extension_switch.track(state, job, dies())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert job.task is not None and job.task.done()
        # the done-callback closes the job, so no page has to notice first
        assert not job.running, "a job whose task ended is still reported as running"
        assert extension_switch.running(state, "dcs-a-01") == []
        taken = extension_switch.take_results(state, "dcs-a-01")
        assert [entry.ok for entry in taken] == [False]
        assert "stopped before it" in taken[0].message

    asyncio.run(run())


@pytest.mark.asyncio
async def test_the_finished_job_becomes_the_notice_once(switch_dir, node_data):  # noqa: F811
    """A refusal (a failed install) arrives as the page's own notice — the sentence the blocking
    switch used to return inline."""
    app = _app(switch_dir)
    job = extension_switch.begin(app.state, "dcs-a-01", "dcs-a-01", "Syria", "Olympus", "enable")
    extension_switch.finish(app.state, job, False,
                            "Extension 'Olympus' could not be enabled on server 'Syria': boom")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        first = await client.get(TAB)
        second = await client.get(TAB)
    assert "could not be enabled" in first.text
    assert "could not be enabled" not in second.text
