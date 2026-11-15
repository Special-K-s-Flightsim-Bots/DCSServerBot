"""The dialog closes the moment the primary button is pressed (a regression, put back).


    *\"If I press the startup or shutdown server glyph, and then on startup or shutdown in the modal,
      the modal does not close and the button starts blinking. But the modal should close, the moment
      I press that button. We had that before, but that is in again.\"*

WHAT WAS WRONG. The console's ONE interceptor (``static/submit.js``) sent the dialog's POST with
``redirect: \"manual\"`` and navigated to the page the dialog states only ONCE THE WRITE HAD ANSWERED —
a wait added for the delete-refresh case, and applied to the DIALOG as well. Because a real
startup/shutdown is slow, the dialog therefore stayed on screen and its button wore the ``busy``
idiom (\"starts blinking\") for the whole write — exactly the frozen modal the operator saw. The routing table
was checked FIRST and is NOT the cause: ``POST /actions/server/startup`` and
``.../startup/confirm`` are registered as POST routes (``pages/actions.add_routes``) and answer
(303 / 200) — nothing shadows them, and the POST both fires and answers.

THE FIX (one file, ``static/submit.js``). The form itself says what it is: a form that STATES where it
returns to (``data-back``) IS the dialog being dismissed, so it leaves AT ONCE on the press, with the
write still running in the background (``keepalive``). A form that does NOT state a ``data-back`` (the
strip's direct controls, the config/revert/channels/coalitions forms) stays on its page and keeps the
old rule — it navigates once the write has answered, so the fresh render reads the post-write state.

THE PIN. The behaviour lives in the shipped script, so it is asserted over the REAL script through the
browser-less harness (``submit_harness.js``, ``tests/test_webui_m4fix2_after_write.py``): before the
fix the dialog's navigation was ABSENT until the answer was resolved; here it must be present the
moment the press is recorded. The route half (the write fires and answers, nothing shadows it) is
driven through the real application.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import back_target

from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page

from test_webui_write_pause import (  # noqa: F401 - fixtures/helpers imported on purpose
    build, clean_registry, client, config_dir, open_page, sign_in, token_of,
)
from test_webui_write_server_actions import fleet as server_fleet, offered

TESTS = Path(__file__).resolve().parent
SUBMIT_JS = TESTS.parent / "services" / "webservice" / "static" / "submit.js"
SUBMIT_HARNESS = TESTS / "submit_harness.js"

CONFIRM = actions_page.CONFIRM_SUFFIX
STARTUP = "/actions/server/startup"
SHUTDOWN = "/actions/server/shutdown"

DASHBOARD = dashboard_page.DASHBOARD_PATH

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def interceptor() -> dict:
    """The shipped submit.js's own answers, driven by the harness over the REAL file."""
    assert SUBMIT_HARNESS.is_file() and SUBMIT_JS.is_file()
    done = subprocess.run(["node", str(SUBMIT_HARNESS), str(SUBMIT_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def form_for(html: str, path: str) -> str:
    match = re.search(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert match, f"the page rendered no control posting to {path}"
    return match.group(0)


def fields_of(form: str) -> dict[str, str]:
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


# ===================================================== 1. the dialog closes on the press (the pin)

@requires_node
def test_the_dialog_closes_the_moment_the_primary_button_is_pressed(interceptor):
    """THE card's headline, over the REAL shipped script.

    A form that states its destination (``data-back``) is the dialog: pressing its primary button
    navigates AT ONCE — the navigation is on the recording BEFORE the write's answer is ever resolved
    — and the write still runs (one request on the wire). BEFORE the fix the navigation was ABSENT at
    that point (``beforeResolve.navigations == []``), which is the frozen modal the operator reported.
    """
    dialog = interceptor["dialog"]["beforeResolve"]
    assert dialog["navigations"] == ["/logs"], \
        "the dialog must leave for its stated page the moment the button is pressed"
    assert dialog["requests"] == 1, "the write is still fired — the close does not cancel it"
    assert dialog["order"] == ["write", "navigate"], \
        "the request is on the wire first, then the page leaves — never a lost write"
    assert dialog["busyClass"] is False, \
        "no busy idiom is worn: there is no visible wait for a dialog that closes on the press"
    # and it stays left: resolving the answer does not re-navigate anywhere else
    assert interceptor["dialog"]["after"] == ["/logs"]


@requires_node
def test_a_form_that_stays_on_its_page_still_waits_for_the_write_to_answer(interceptor):
    """The other half, unchanged: a form with NO ``data-back`` (the strip's direct control, the config
    forms) is not a dialog being dismissed — it stays on its page and navigates only once the write has
    answered, so the fresh render reads the state the action moved. This is the delete-refresh rule the
    fix deliberately did NOT touch."""
    waits = interceptor["waits"]
    assert waits["before"]["navigations"] == [], \
        "a staying form must NOT be handed over before the write answers"
    assert waits["before"]["busyClass"] is True, "the wait is shown on the pressed control"
    assert waits["after"]["order"] == ["write", "navigate"], "write THEN navigate — never alongside"


def test_the_shipped_interceptor_still_sends_the_form_it_was_given():
    """The invariants read off the shipped file: ONE ``fetch`` (nothing retried), the form's own body,
    ``redirect: \"manual\"`` (the 303 is not followed by the promise) and ``keepalive`` (a write started
    from a dialog that leaves at once still finishes server-side). The control is never disabled."""
    source = SUBMIT_JS.read_text(encoding="utf-8")
    assert source.count("fetch(") == 1, "one request per press, nothing retried"
    assert "new FormData(form)" in source, "the form's own body is what is sent"
    assert 'redirect: "manual"' in source, "fetch must not follow the 303 itself"
    assert "keepalive: true" in source, "a write from a dialog that leaves still finishes server-side"
    assert 'getAttribute("data-back")' in source, \
        "the dialog is recognised by the destination it STATES — the form's own marker"
    assert ".disabled" not in source, "the pressed control is never disabled"


# ============================================= 2. the write path is not shadowed (the route answers)

@pytest.mark.asyncio
async def test_the_dialog_write_route_is_not_shadowed_and_answers_the_post(config_dir, clean_registry):
    """THE routing-table check, first candidate: the dialog's write is matched by its OWN POST route —
    not by a greedy ``{name:path}`` sibling and not by a method mismatch.

    Driven through the real application: the row's Startup glyph loads the dialog (200), the dialog's
    form posts the action and gets the normal 303 back — and the SAME path answers a GET with 405
    (a POST-only route), which is what proves a real route answered and nothing swallowed the path.
    """
    cluster = server_fleet(status=Status.SHUTDOWN)          # Startup is offered on a SHUTDOWN server
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        assert STARTUP + CONFIRM in offered(dashboard), "the page offered no Startup control"

        row = fields_of(form_for(dashboard, STARTUP + CONFIRM))
        dialog = await http.post(STARTUP + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
        action_form = form_for(dialog.text, STARTUP)
        assert "data-async" in action_form and "data-back" in action_form, \
            "the dialog's own form is the background write AND states where it returns"

        body = fields_of(action_form)
        body["maintenance"] = "1"
        accepted = await http.post(STARTUP, data=body)
        assert accepted.status_code == 303, (accepted.status_code, accepted.text)
        assert accepted.headers["location"] == DASHBOARD, \
            "the write returns to the page the dialog states (`data-back`)"

        # the same path is POST-only: a GET is a method mismatch, not a silent handler
        wrong_method = await http.get(STARTUP)
        assert wrong_method.status_code == 405

    assert cluster.servers[0].calls == ["startup"], "the write ran exactly once"


@pytest.mark.asyncio
async def test_the_shutdown_dialog_also_states_where_it_returns(config_dir, clean_registry):
    """The second of the pair the operator named: the Shutdown dialog (a RUNNING server) carries the same two
    attributes, so it closes on the press by the same rule — the fix is shared, not per-glyph."""
    cluster = server_fleet(status=Status.RUNNING)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        assert SHUTDOWN + CONFIRM in offered(dashboard)
        row = fields_of(form_for(dashboard, SHUTDOWN + CONFIRM))
        dialog = await http.post(SHUTDOWN + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
    action_form = form_for(dialog.text, SHUTDOWN)
    assert "data-async" in action_form
    assert back_target(action_form, SHUTDOWN + CONFIRM) == DASHBOARD, \
        "the dialog states the page it returns to"
    assert token_of(dialog.text), "the dialog re-emits the session's CSRF token"
