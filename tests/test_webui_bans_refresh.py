"""The Bans page's two view/refresh defects from the test-PC run.

* **Item 3 — the list must REFRESH after a lift or an add.** The write dialogs are the console's ONE
  ``confirm.html`` component, and the console's ONE interceptor (``static/submit.js``) hands the page
  over the moment the primary button is pressed — the GET that renders the returned page therefore
  races the still-running write, and the Bans page (a plain list page, no live stream) never corrects
  it. The ban dialogs therefore carry ``data-await``: the SAME stated destination, reached only once
  the route has committed the write. The harness drives the REAL shipped script (a markup assertion
  cannot see a race); the server-side tests pin the attribute and the write's 303 target.
* **Item 4 — cancelling the DGSA lift dialog must KEEP the DGSA view.** The row control carries the
  page's current view option (``dgsa=1``) as a hidden field, the dialog re-emits it into its
  ``Cancel``/``data-back`` URL, and the write's redirect carries it back — so the dialog and the
  write both return to the state the operator was reading.

Driven through the REAL application and the stub ServiceBus, like the ban suites this file extends.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import back_target

from services.servicebus import ServiceBus
from services.webservice.pages import actions as actions_page
from services.webservice.pages import bans as bans_page
from services.webservice.readmodels.bans import DGSA_ON, DGSA_PARAM
from test_webui_bans_page import (  # noqa: F401 - the ban harness, reused on purpose
    FakeBus, LATER, NOW, _row, ban_bus, bans_page_html,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures reused on purpose
    build, clean_registry, client, config_dir, fleet, recording_bot, sign_in, token_of,
)

TESTS = Path(__file__).resolve().parent
SUBMIT_JS = TESTS.parent / "services" / "webservice" / "static" / "submit.js"
SUBMIT_HARNESS = TESTS / "submit_harness.js"

LIFT = actions_page.BAN_LIFT_PATH
LIFT_DIALOG = LIFT + actions_page.CONFIRM_SUFFIX
ADD = actions_page.BAN_ADD_PATH
ADD_DIALOG = ADD + actions_page.CONFIRM_SUFFIX
DGSA_VIEW = f"{bans_page.BANS_PATH}?{DGSA_PARAM}={DGSA_ON}"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


# ------------------------------------------------------------- helpers

def _form(html: str, path: str) -> str:
    match = re.search(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert match, f"the page rendered no control posting to {path}"
    return match.group(0)


def _forms(html: str, path: str) -> list[str]:
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


def _control_for(html: str, path: str, **wanted: str) -> str:
    """The ONE control posting to *path* whose hidden fields match *wanted*."""
    for form in _forms(html, path):
        fields = _fields(form)
        if all(fields.get(name) == value for name, value in wanted.items()):
            return form
    raise AssertionError(f"no control posting to {path} carrying {wanted}")


def _fields(form: str) -> dict[str, str]:
    """The form's hidden fields, as the body the browser would send it."""
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


@pytest.fixture(scope="module")
def interceptor() -> dict:
    """The shipped ``submit.js``'s own answers, driven by the harness over the REAL file."""
    assert SUBMIT_HARNESS.is_file() and SUBMIT_JS.is_file()
    done = subprocess.run(["node", str(SUBMIT_HARNESS), str(SUBMIT_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def live_bus():
    """A stub bus whose ``bans()`` drops EXPIRED rows, exactly like the real ``ServiceBus.bans``.

    The page's own stub keeps every row so a lift can be inspected on the bus; the landed page's
    freshness can only be asserted against a bus that behaves like production — a lifted row
    (``banned_until`` in the past) is not in the list any more.
    """

    class FilteringBus(FakeBus):
        async def bans(self, *, expired=False, servers=None):
            rows = await super().bans(expired=expired, servers=servers)
            return [row for row in rows if row["banned_until"] > NOW]

    from core.services.registry import ServiceRegistry

    saved = ServiceRegistry._singletons.get(ServiceBus)
    bus = FilteringBus([])
    ServiceRegistry._singletons[ServiceBus] = bus
    try:
        yield bus
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


# ------------------------------------------------------------- 1. item 3, the interceptor's half

@requires_node
def test_the_waiting_dialog_hands_the_page_over_only_after_the_write(interceptor):
    """THE defect: a ``data-await`` dialog keeps its destination but waits for the write's answer.

    BEFORE the fix the interceptor branched on ``data-back`` alone, so a Bans dialog navigated at once
    and the list it landed on was rendered BEFORE the route had committed the write. The harness drives
    the REAL script: after the submit the request is on the wire, the control wears the busy idiom, and
    NO navigation has happened — the navigation to the stated page appears only on the answer.
    """
    waits = interceptor["awaitDialog"]
    before = waits["beforeResolve"]
    assert before["requests"] == 1, "exactly one request per press"
    assert before["navigations"] == [], "a data-await dialog must NOT be handed over before the answer"
    assert before["busyClass"] is True, "the wait is shown on the pressed control"
    assert before["order"] == ["write"], "the write is fired first and nothing navigates yet"
    assert waits["stillBefore"] == [], "still no navigation after a tick: the wait is real"
    assert waits["after"] == ["/bans?dgsa=1"], "the waiting dialog lands on the page it states"


@requires_node
def test_the_ordinary_dialog_still_closes_on_the_press(interceptor):
    """The pin the fix must NOT break: a dialog without ``data-await`` still leaves on the press.

    A server's Startup/Shutdown dialog keeps the immediate close (the operator's own rule); only the
    Bans dialogs opt into waiting.
    """
    dialog = interceptor["dialog"]["beforeResolve"]
    assert dialog["navigations"] == ["/logs"], "the ordinary dialog must still close on the press"
    assert dialog["busyClass"] is False, "and wear no busy idiom"


# ------------------------------------------------------------- 2. item 3, the server's half

@pytest.mark.asyncio
async def test_the_bans_dialogs_await_their_write(config_dir, clean_registry, ban_bus):
    """Each ban dialog's form states its destination AND asks the interceptor to wait for the write."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        lift = await http.post(LIFT_DIALOG, data={"ucid": "u-a", "banned_at": NOW.isoformat(),
                                                  "_csrf_token": token_of(page)})
        add = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
        edit = await http.post(actions_page.BAN_EDIT_PATH + actions_page.CONFIRM_SUFFIX,
                               data={"ucid": "u-a", "banned_at": NOW.isoformat(),
                                     "_csrf_token": token_of(page)})
    for label, dialog, path in (("lift", lift, LIFT), ("add", add, ADD),
                                ("edit", edit, actions_page.BAN_EDIT_PATH)):
        assert dialog.status_code == 200, (label, dialog.text)
        form = _form(dialog.text, path)
        assert "data-async" in form and "data-back=" in form, f"{label}: the dialog lost its opt-in"
        assert "data-await" in form, \
            f"{label}: the dialog would race its own write — the list would render pre-write"


@pytest.mark.asyncio
async def test_a_lift_lands_on_a_list_that_no_longer_holds_the_ban(config_dir, clean_registry,
                                                                   live_bus, recording_bot):
    """The write's 303 is answered only AFTER the lift, so following it lands on the post-write list."""
    live_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1"),
                     _row("u-b", name="Bravo", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        assert "Alpha" in page and "Bravo" in page
        row = _fields(_control_for(page, LIFT_DIALOG, ucid="u-a"))
        dialog = await http.post(LIFT_DIALOG, data=row)
        token = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(LIFT, data={**row, "_confirm_token": token})
        assert reply.status_code == 303, reply.text
        landed = await http.get(reply.headers["location"])
    assert landed.status_code == 200
    assert "Alpha" not in landed.text, "the page the write returns to still lists the lifted ban"
    assert "Bravo" in landed.text, "the rest of the list vanished"


# ------------------------------------------------------------- 3. item 4, the DGSA view is kept

@pytest.mark.asyncio
async def test_the_row_control_carries_the_dgsa_view(config_dir, clean_registry, ban_bus):
    """With the DGSA rows shown, every row control re-emits ``dgsa=1`` so its dialog can return to it."""
    ban_bus.rows = [_row("u-d", name="GlobalOne", server_name=None, banned_by="cloud"),
                    _row("u-a", name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        shown = (await http.get(DGSA_VIEW)).text
        plain = await bans_page_html(http)
    shown_forms = _forms(shown, LIFT_DIALOG)
    assert len(shown_forms) == 2, "the shown view did not render both rows' controls"
    for form in shown_forms:
        assert _fields(form).get(DGSA_PARAM) == DGSA_ON, \
            "a row control did not carry the view option"
    plain_forms = _forms(plain, LIFT_DIALOG)
    assert plain_forms, "the plain view rendered no control at all"
    for form in plain_forms:
        assert DGSA_PARAM not in _fields(form), \
            "a control on the plain view carried a view option nobody chose"


@pytest.mark.asyncio
async def test_cancelling_the_dgsa_lift_dialog_keeps_the_dgsa_view(config_dir, clean_registry,
                                                                   ban_bus):
    """THE defect: the dialog's ``Cancel`` (and its ``data-back``) must carry the ``dgsa`` param."""
    ban_bus.rows = [_row("u-d", name="GlobalOne", server_name=None, banned_by="cloud")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = (await http.get(DGSA_VIEW)).text
        dialog = await http.post(LIFT_DIALOG, data=_fields(_form(page, LIFT_DIALOG)))
    assert dialog.status_code == 200, dialog.text
    assert back_target(dialog.text, LIFT_DIALOG) == DGSA_VIEW, \
        "the dialog's own return dropped the DGSA view"


@pytest.mark.asyncio
async def test_a_lift_made_in_the_dgsa_view_returns_to_it(config_dir, clean_registry, ban_bus,
                                                          recording_bot):
    """The write's redirect keeps the view option, so the DGSA rows do not vanish under the operator."""
    ban_bus.rows = [_row("u-d", name="GlobalOne", server_name=None, banned_by="cloud"),
                    _row("u-a", name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = (await http.get(DGSA_VIEW)).text
        row = _fields(_control_for(page, LIFT_DIALOG, ucid="u-d"))
        dialog = await http.post(LIFT_DIALOG, data=row)
        token = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(LIFT, data={**row, "_confirm_token": token})
    assert reply.status_code == 303, reply.text
    assert reply.headers["location"] == DGSA_VIEW, \
        "the write returned to the page without the view the operator was reading"
    assert ban_bus.active("u-d") == []
