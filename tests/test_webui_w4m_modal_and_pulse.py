"""The modal closes on press, and the pulse is a SIGNAL rather than a race.

Two defects: the dialog waited for the action's answer before vanishing (``static/submit.js`` branched
on ``data-notice``), which froze the page; and the expectation rode a cookie that arrived after the
render. Both controls now take ONE path, and the refusal classes the route makes (no signed-in
identity, a spent/absent confirm token, an out-of-scope target) store a ONE-SHOT NOTICE before they
refuse, so a background submit's refusal is seen on the page the person lands on. The expectation is
held PROCESS-side, keyed by target, written BEFORE the action runs and ended when its observable moves
/ on a non-success / at the ceiling. Driven through the real app."""
from __future__ import annotations

import re

import pytest
from conftest import back_target

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page

from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, open_page, recording_bot, sign_in, token_of,
)

DASHBOARD = dashboard_page.DASHBOARD_PATH
RESTART = "/actions/server/restart"
PAUSE = actions_page.PAUSE_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX

#: the accessible signal of the control's pulse (shared by copy, not import, so other files stay as they are).
#: left as they are).
BUSY = 'aria-busy="true"'


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path* — a control, with its button and its own signals."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


def fields_of(form: str) -> dict[str, str]:
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


async def notice_fragment(http) -> str:
    """The one-shot notice as the LIVE path renders it — the fragments the page polls."""
    reply = await http.get(live_page.DASHBOARD_FRAGMENTS_PATH + "?level=all")
    assert reply.status_code == 200, reply.text
    return reply.json().get(live_page.NOTICE_TARGET, "")


# ============================================ 1. the dialog's wait is GONE (Defect 1, markup)

@pytest.mark.asyncio
async def test_the_dialog_has_no_refusal_line_and_its_form_does_not_wait(config_dir, clean_registry):
    """THE card's Defect 1, at the markup: the dialog renders no notice line, and its form carries
    ``data-back`` but NO ``data-notice`` — the attribute the interceptor used to branch on to make the
    dialog wait. Both controls take one path now."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        dialog = await http.post(RESTART + CONFIRM,
                                 data=fields_of(form_for(dashboard, RESTART + CONFIRM)))
    assert dialog.status_code == 200, dialog.text
    html = dialog.text
    form = form_for(html, RESTART)
    assert "data-async" in form, "the dialog's own form is the background write"
    assert back_target(form, RESTART + CONFIRM) == DASHBOARD, \
        "and it knows the page it hands back to"
    assert "data-notice" not in form, "the dialog must not wait on a notice line any more"
    assert 'id="dlg-notice"' not in html, "nor render one: a refusal rides the one-shot notice"
 # the keyboard contract and the no-JS path are untouched
    assert 'role="dialog"' in html and "autofocus" in html
    assert 'name="_confirm_token"' in form and 'name="_csrf_token"' in form


# ============================================ 2. the route refusals reach the person (Defect 1)

@pytest.mark.asyncio
async def test_a_replayed_confirm_token_leaves_a_one_shot_notice(config_dir, clean_registry,
                                                                 recording_bot):
    """A SPENT confirm token.

    The token is minted for one dialog render and spent by the POST that uses it, so a replayed form is
    refused (403). What is new is that the refusal is STORED: the live path carries it to the person on
    the page the control came from.
    """
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        dialog = await http.post(RESTART + CONFIRM,
                                 data=fields_of(form_for(dashboard, RESTART + CONFIRM)))
        body = fields_of(form_for(dialog.text, RESTART))

        first = await http.post(RESTART, data=body)
        assert first.status_code == 303, "the first press runs (and spends the token)"
 # the landing render may or may not have seen it — pop anything the page itself showed
        landed = await open_page(http, DASHBOARD)
        assert "restarted" in landed or "Server" in landed

        replayed = await http.post(RESTART, data=body)           # the SAME form, sent again
        assert replayed.status_code == 403, "a spent token is refused, exactly as before"

        notice = await notice_fragment(http)
    assert notice, "the refusal must be delivered by the live path ()"
    assert 'class="notice bad"' in notice and "notice-msg" in notice
    assert "must be confirmed through its dialog" in notice
    assert "SRS-1" not in notice, "the refusal names no target"


@pytest.mark.asyncio
async def test_an_out_of_scope_write_leaves_a_notice_that_names_nothing(config_dir, clean_registry,
                                                                       recording_bot):
    """A target outside the caller's scope: the console refuses with its own bare 403 (the SAME answer as a
    name that does not exist -- no enumeration).

    The stored notice says exactly as much and no more, and never names the target.
    """
    cluster = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "hoster-b")            # manages Host B
        page = await open_page(http)
        assert 'value="SRS-1"' not in page, "no control on somebody else's row"

        reply = await http.post(PAUSE, data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 403
        assert "SRS-1" not in reply.text, "the refusal itself still discloses nothing"

        notice = await notice_fragment(http)
    assert notice, "an out-of-scope refusal must also be delivered ()"
    assert "outside your scope" in notice
    assert "SRS-1" not in notice, "and the notice names no target either"
 # nothing ran
    assert cluster.servers[0].current_mission.calls == [], "a refused request reaches no action"


@pytest.mark.asyncio
async def test_a_write_without_an_identity_leaves_the_same_notice(config_dir, clean_registry):
    """The third refusal class: no signed-in identity at the handler.

    It is DEFENSIVE in production — the app-level gate refuses an anonymous write before the handler
    runs — but the branch is real (a role resolver that answers without an identity), so it must not
    be the one refusal that vanishes. ``_identity`` is patched to answer ``None`` for this one call
    (the gate resolves roles through its own, untouched path).
    """
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        token = token_of(page)

        original = actions_page._identity
        actions_page._identity = lambda request: None
        try:
            reply = await http.post(PAUSE, data={"server": "SRS-1", "_csrf_token": token})
        finally:
            actions_page._identity = original
        assert reply.status_code == 403, reply.text

        notice = await notice_fragment(http)
    assert notice, "a refusal must never be the one class that vanishes ()"
    assert "no signed-in identity" in notice


# ============================================ 3. a failed write leaves no pulse

@pytest.mark.asyncio
async def test_a_failed_write_leaves_no_pulse(config_dir, clean_registry):
    """A start that FAILED must not blink beside the notice that says it failed.

    The expectation is written BEFORE the action runs, so the route must DROP it when the action does
    not succeed.
    """
    import types

    from core import Status
    from test_webui_busy_await_change import booting_server
    from test_webui_write_pause import Cluster, instance

    async def exploding_startup(modify_mission=True, use_orig=True, preset=None):  # noqa: ARG001
        raise TimeoutError("dcs never came up")

    a = booting_server("SRS-1", ["Host A"], status=Status.SHUTDOWN, startup=exploding_startup)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    cluster = Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                      instances=[instance("Host-A-Instance", a)])
    app = build(config_dir, cluster)
    startup = next(action.path for action in actions_page.SERVER_ACTIONS if action.key == "startup")

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        reply = await http.post(startup, data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 303, "a typed refusal is a 303 + a notice"
        after = await open_page(http)

    assert actions_page.awaiting_store() == {}, "a failed write drops its expectation at once"
    assert BUSY not in after, "and the row must NOT be pulsing"
    assert "Timeout while starting up" in after, "the notice says what happened"


# ============================================ 4. a spent entry can never RE-ARM (found in a browser)

def test_the_row_read_spends_a_settled_expectation_and_a_transitional_one_keeps_it():
    """A restart submitted in a settled state.

    STOPPED and LOADING are TRANSITIONAL for a restart and no longer spend the expectation; the SETTLED
    state (RUNNING/PAUSED) does. The anti-re-arm guarantee is kept by the ``departed`` latch: once the
    row has been seen away from its submitted state, the return ENDS the pulse rather than re-arming it.
    So the sibling read at STOPPED answers ``False`` (a sibling never pulses) while the entry survives;
    the read at RUNNING spends it, whoever asks.
    """
    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    restart = by_key["restart"]
    actions_page.remember_awaiting_change("server", "SRS-1", restart, "RUNNING", False)

 # a SIBLING control's read (the row is STOPPED: Start is what is offered) does NOT pulse...
    assert actions_page.awaiting_change("server", "SRS-1", "start",
                                       "STOPPED", False) is False
 #...and, being transitional, does NOT spend the restart's own expectation either
    assert actions_page.awaiting_store() != {}, "a transitional state must not spend the expectation"
    assert actions_page.awaiting_change("server", "SRS-1", "restart",
                                       "LOADING", False) is True, "LOADING is the wait, not the finish"

 # the row reaches its SETTLED state: the expectation is spent — and DROPPED, whoever asked
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "RUNNING", False) is False
    assert actions_page.awaiting_store() == {}, "a settled expectation is dropped, whoever asked"

 #...and the row coming back to the submitted value must NOT pulse again (no re-arm)
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "RUNNING", False) is False
