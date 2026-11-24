"""The strip controls submit in the background, so the row pulse becomes visible.

The pulse is stamped on the row SERVER-SIDE and only exists on a RENDERED row, but a strip control was
a plain ``<form method="post">``, so the browser left the page and waited and no rendered row existed
while the action ran. Pins: the strip's DIRECT control carries ``data-async`` while still being a real
POST form (the session token, target and origin stay in the body); a control that only OPENS a dialog
stays a plain form (pinned for both surfaces); one interceptor (``static/submit.js``, served and loaded
by ``base.html``) fires the POST in the background and hands the page over ONCE THE WRITE HAS ANSWERED
— and never disables the pressed control; the row that was pressed marks itself (``data-busy`` on the
strip container) driven through the REAL action seam; and a refused write's one-shot notice reaches the
person through the LIVE path, with the notice target omitted when there is nothing to say. Driven
through the real app."""
from __future__ import annotations

import re
import types

import pytest

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import servers as servers_page

from test_webui_write_pause import (  # noqa: F401  (fixtures are used by name)
    build, clean_registry, client, config_dir, fleet as pause_fleet, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_busy_control import in_flight, running_fleet
from test_webui_node_controls import clean_node_registry, fleet as node_fleet  # noqa: F401

PAUSE = actions_page.PAUSE_PATH
UNPAUSE = actions_page.UNPAUSE_PATH
RESTART = "/actions/server/restart"
NODE_RESTART = "/actions/node/restart"
CONFIRM = actions_page.CONFIRM_SUFFIX
ORIGIN = actions_page.ORIGIN_FIELD

DASHBOARD = dashboard_page.DASHBOARD_PATH
SERVERS = servers_page.SERVERS_PATH
NODES = nodes_page.NODES_PATH
SUBMIT_JS = "/static/submit.js"


# ---------------------------------------------------------------------------------- helpers

def forms_for(html: str, path: str) -> list[str]:
    """Every rendered ``<form>`` that posts to *path* (a row usually has exactly one)."""
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


def form_for(html: str, path: str) -> str:
    found = forms_for(html, path)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


def fields_of(form: str) -> dict[str, str]:
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


def async_forms(html: str) -> list[str]:
    """Every form on the page that opted into the background submit."""
    return re.findall(r"<form[^>]*data-async[^>]*>", html)


# ================================================== 1. the strip's direct control is async

@pytest.mark.asyncio
async def test_the_strips_direct_control_is_a_background_write(config_dir, clean_registry):
    """The Startup/Pause form is intercepted, so the browser fires the POST in the background and hands
    the page over ONCE THE WRITE HAS ANSWERED — the fresh render then reads the state the action moved.

    It stays a REAL form and the request is byte-for-byte unchanged: ``method="post"``, the session's
    CSRF field, the TARGET and the ORIGIN in the body, never a URL parameter.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        standing = await open_page(http, SERVERS)

    for page, label in ((dashboard, "the Dashboard"), (standing, "/fleet")):
        form = form_for(page, PAUSE)
        assert "data-async" in form, f"{label} must submit the strip control in the background"
        assert 'method="post"' in form, "the no-JS path must stay a real POST form"
        fields = fields_of(form)
        assert fields.get("server") == "SRS-1", "the target still travels in the body"
        assert actions_page.ORIGIN_FIELD in fields, "the origin still travels in the body"
        assert fields.get(actions_page.session.CSRF_FIELD), "the session token is still sent"
        assert "?server=" not in page, "a target must never travel in a URL"


@pytest.mark.asyncio
async def test_the_startup_control_pressed_now_opens_its_dialog(config_dir, clean_registry):
    """**Startup** on a SHUTDOWN server.

    Startup grew the maintenance option (Shutdown/Startup mirror Discord's flag defaults), so -- like
    every node control -- it posts to its DIALOG and the option is chosen there; the dialog's OWN form
    is the background write. The row form only LOADS the dialog (instant) and the action runs from the
    dialog's ``data-async`` form.
    """
    from core import Status
    from test_webui_write_pause import Cluster, instance, server as pause_server

    stopped = pause_server("SRS-1", ["Host A"], status=Status.SHUTDOWN)
    node = types.SimpleNamespace(name="dcs-a-01", master=True,
                                 instances={"Host-A-Instance": instance("Host-A-Instance", stopped)})
    cluster = Cluster([stopped], nodes={"dcs-a-01": node}, node=node,
                      instances=[instance("Host-A-Instance", stopped)])
    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")

    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, DASHBOARD)
        row = form_for(html, startup.path + CONFIRM)
        dialog = await http.post(startup.path + CONFIRM, data=fields_of(row))

    assert "data-async" not in row, "the row form only loads the dialog — it is not the write"
    assert dialog.status_code == 200, dialog.text
    action_form = form_for(dialog.text, startup.path)
    assert "data-async" in action_form, "the dialog's own form is the background write"
    assert 'name="maintenance" value="1"' in dialog.text, "the option this card added"
    assert 'name="maintenance" value="0"' in dialog.text, "the OFF companion is required"


# ================================================== 2. a dialog-opening control stays plain

@pytest.mark.asyncio
async def test_the_strips_dialog_control_is_not_a_background_write(config_dir, clean_registry):
    """Restart/Shutdown/Stop post to their DIALOG's path — the answer IS the dialog page. A background
    submit there would navigate away from the dialog the person asked to see, so the form must stay
    plain. Pinned because the strip renders both kinds through ONE template (``_strip.html``).
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        standing = await open_page(http, SERVERS)

    for page in (dashboard, standing):
        loader = form_for(page, RESTART + CONFIRM)
        assert "data-async" not in loader, \
            "a control that loads its dialog must stay a plain form"
 # and the row's DIRECT control on the same page IS async — both kinds, one component
        assert "data-async" in form_for(page, PAUSE)


@pytest.mark.asyncio
async def test_every_node_control_stays_a_plain_form(config_dir, clean_node_registry):
    """EVERY node control posts to its dialog's path by construction (the trio confirms, the
    maintenance pair collects its own option there), so none of them is a background write.
    """
    app = build(config_dir, node_fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    node_forms = [form for form in async_forms(html)]
    assert node_forms == [], "a node control loads a dialog and must not be submitted in the background"
    assert form_for(html, NODE_RESTART + CONFIRM)


# ================================================== 3. one interceptor, on every page

@pytest.mark.asyncio
async def test_the_one_interceptor_is_loaded_on_every_page(config_dir, clean_registry):
    """The strip is on EVERY page, so the interceptor is loaded by ``base.html`` (which the
    confirmation dialog also extends) rather than by one page's markup. The dialog's inline block is
    gone: one copy, one place for the behaviour to be right.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        standing = await open_page(http, SERVERS)
        dialog = await http.post(RESTART + CONFIRM, data=fields_of(form_for(dashboard,
                                                                            RESTART + CONFIRM)))
        script = await http.get(SUBMIT_JS)
    assert script.status_code == 200, "the shared interceptor must be served as an asset"
    for page in (dashboard, standing, dialog.text):
        assert f'src="{SUBMIT_JS}"' in page, "every page must load the ONE interceptor"
    assert 'fetch(form.action' not in dialog.text, "the dialog's inline copy must be gone"
    assert "new FormData(form)" not in dialog.text, "the dialog's inline copy must be gone"


@pytest.mark.asyncio
async def test_the_interceptor_is_the_one_submit_path_for_every_control(
        config_dir, clean_registry):
    """The ONE submit path: a form carrying ``data-async`` (the strip's direct control AND the dialog's
    own action form) fires its POST in the background and hands the page over ONCE THE WRITE HAS
    ANSWERED, and it does NOT disable the pressed control -- the SERVER says what is running and the
    seam's guard refuses a second press.

    There is ONE path and no waiting branch of the dialog's own: a refusal rides the one-shot notice
    on the page they land on. The ORDER of the navigation (after the answer, never alongside) is
    pinned by the browser-less harness over this shipped script, in
    ``tests/test_webui_m4fix2_after_write.py``.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        await open_page(http, DASHBOARD)
        script = await http.get(SUBMIT_JS)
    body = script.text
 # ONE path: fire the POST, then leave ONCE IT HAS ANSWERED — no branch on a notice line
    assert "window.location.assign(to)" in body
    assert "window.location.pathname" in body, \
        "a strip write returns to the page it was submitted from (path + query, no fragment)"
    assert "reply.then(" in body, "the navigation rides the write's answer, not the press"
    assert "if (!line)" not in body, "the dialog must not have a waiting branch any more"
    assert "opaqueredirect" not in body, "nothing waits for the reply: the refused body is not read"
    assert "refuse(" not in body, "the dialog's own refusal line is gone"
 # one request, nothing retried
    assert body.count("fetch(") == 1
 # and the pressed control is never disabled (the guard is the answer to a double click)
    assert ".disabled" not in body, "the console must not disable the control it just pressed"


@pytest.mark.asyncio
async def test_the_strip_form_still_posts_without_javascript(config_dir, clean_registry):
    """The enhancement's fallback: with JavaScript off the strip form is a real POST to the action's
    route with the session token — today's behaviour, unchanged.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
    form = form_for(dashboard, PAUSE)
    assert 'method="post"' in form
    assert 'name="_csrf_token"' in form, "the no-JS POST needs the session token"
    assert 'name="server" value="SRS-1"' in form


# ================================================== 4. the marker, on the row that was pressed

@pytest.mark.asyncio
async def test_the_row_marks_itself_while_a_target_is_in_flight(config_dir, clean_registry):
    """With a target in flight through the REAL seam, the FRESH RENDER the at-once navigation lands on shows
    the row as running.

    It shows it on the ROW (a ``busy`` class + ``data-busy`` on the strip container) and NOT on the
    control's form: the seam keys the target and cannot say which action runs, so no glyph may claim it.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")

        rest = await open_page(http, DASHBOARD)
        assert 'data-busy="true"' not in rest, "the pre-flight render must be clean"

        async with in_flight("SRS-1"):
            flying = await open_page(http, DASHBOARD)
        form = form_for(flying, PAUSE)
        assert "data-async" in form, "the background control is the one whose row marks itself"
        assert 'data-busy="true"' in flying, "the ROW must carry the marker while its target is busy"
        assert 'aria-busy="true"' not in form, \
            "no control claims the action: the seam cannot say which one is running"

        ended = await open_page(http, DASHBOARD)
        assert 'data-busy="true"' not in ended, "the guard cleared, so the marker must be gone"


# ================================================== 5. the outcome reaches the person

@pytest.mark.asyncio
async def test_a_refused_write_is_delivered_by_the_live_path(config_dir, clean_registry,
                                                             recording_bot):
    """THE card's item 2. A write's page and its outcome must not depend on the person reloading: the
    LIVE path (the fragments the polling fallback and the stream read) carries a refusal's one-shot
    notice without a manual refresh — and it must, because the live transport re-renders a page that
    is already open, whatever the write did before it landed.

    Driven through the REAL application: a refused write (a stale Unpause on a RUNNING server) stores
    its one-shot notice, and the fragments endpoint the client polls returns it under the ``notice``
    target.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)

 # a refusal the ACTION makes (the page offered no Unpause — a stale page meets the guard)
        reply = await http.post(UNPAUSE, data={"server": "SRS-1", "_csrf_token": token_of(dashboard),
                                               ORIGIN: DASHBOARD})
        assert reply.status_code == 303, reply.text

        fragments = await http.get(live_page.DASHBOARD_FRAGMENTS_PATH + "?level=all")
    assert fragments.status_code == 200, fragments.text
    targets = fragments.json()
    assert live_page.NOTICE_TARGET in targets, \
        "a refused write's outcome must ride the live path to the person"
    notice = targets[live_page.NOTICE_TARGET]
    assert 'class="notice bad"' in notice, "a refusal is shown in the console's own vocabulary"
    assert "notice-msg" in notice


@pytest.mark.asyncio
async def test_the_streams_snapshot_carries_the_outcome_when_it_is_already_stored(
        config_dir, clean_registry, recording_bot):
    """The dashboard's PRIMARY transport is the EventSource, so the outcome must ride it too — and it
    does for any outcome already stored when the stream connects: the fresh render the navigation
    lands on opens the stream, and the snapshot the client swaps in carries the notice.

    Driven over the REAL stream endpoint. The stream is made finite the way ``test_webui_live`` does it:
    the (single) client slot is filled first, so this client gets its snapshot and the stream closes
    cleanly instead of ticking forever.
    """
    app = build(config_dir, running_fleet())
    app.state.webui_config["dashboard"] = {"live": True, "stream_clients": 1}
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)

 # a refusal, stored in the session BEFORE the stream is opened (the at-once landing's case)
        reply = await http.post(UNPAUSE, data={"server": "SRS-1", "_csrf_token": token_of(dashboard),
                                               ORIGIN: DASHBOARD})
        assert reply.status_code == 303, reply.text

        registry = live_page.get_registry(app, live_page.dashboard_config(app))
        assert registry.try_acquire() is True, "the only slot is taken before the request"
        try:
            response = await http.get(live_page.DASHBOARD_STREAM_PATH + "?level=all",
                                      headers={"accept": "text/event-stream"})
        finally:
            registry.release()
    assert response.status_code == 200
    body = response.text
    assert body.startswith("event: snapshot\n"), body[:120]
    assert '"notice"' in body, "the snapshot must carry the one-shot notice to the person"
    assert "notice bad" in body and "notice-msg" in body


@pytest.mark.asyncio
async def test_a_frame_with_nothing_to_say_can_never_blank_the_notice(config_dir, clean_registry,
                                                                     recording_bot):
    """The other half of the same rule: the notice is ONE-SHOT and the live path renders many times, so
    a frame with nothing to say OMITS the target rather than sending an empty one — otherwise the
    first poll after a page load would blank the notice the page's own render had just shown
    (``static/shell.js`` skips an id absent from a frame).
    """
    app = build(config_dir, pause_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        reply = await http.post(PAUSE, data={"server": "SRS-1", "_csrf_token": token_of(dashboard),
                                             ORIGIN: DASHBOARD})
        assert reply.status_code == 303
 # the PAGE a normal (no-JS) write lands on pops the notice and shows it in the markup…
        landed = await open_page(http, DASHBOARD)
        assert 'class="notice' in landed and "paused" in landed
 # …so the next live frame must carry NO notice target at all
        fragments = await http.get(live_page.DASHBOARD_FRAGMENTS_PATH + "?level=all")
    assert live_page.NOTICE_TARGET not in fragments.json(), \
        "an empty notice must be omitted so a frame cannot blank the page's own notice"
