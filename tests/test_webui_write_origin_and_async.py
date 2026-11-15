"""The write redirect keeps the page it was pressed on, and the confirm dialog does not hang.

Defect 1: the redirect must return to the page the write was submitted FROM. The Dashboard renders the
SAME strips the standalone pages do, so a Startup pressed there used to land the person on ``/fleet``.
The origin travels as a hidden field and the route resolves it by LOOKUP against the registrar's own
registry of known pages, so a request can name a page of this console and NOTHING else (the
open-redirect safety line). Defect 2: the confirmation dialog must not hold the page while the bot
works -- it is a real form (with JavaScript off the POST and the 303 are unchanged), and its script
intercepts the submit, POSTs the SAME body with ``fetch`` and hands the page over ONCE THE WRITE HAS
ANSWERED, delivering a refusal through the console's one-shot notice. Pins the rendered page, the
route's origin allow-list, and exactly ONE ``fetch`` (nothing retries the one-shot token). Driven
through the real app and a stub cluster."""
from __future__ import annotations

import re
from urllib.parse import urljoin

import pytest
from conftest import back_target

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page

# ONE harness for the console's write actions: the pause pilot's doubles and fixtures are reused.
from test_webui_write_pause import (  # noqa: F401  (fixtures are used by name)
    build, clean_registry, client, config_dir, open_page, recording_bot, sign_in, token_of,
)
from test_webui_write_server_actions import fleet as running_fleet
from test_webui_write_player_actions import player_fleet
from test_webui_write_message import UCID_A
from test_webui_node_controls import clean_node_registry, fleet as node_fleet  # noqa: F401
from test_webui_node_maintenance import clean_node_registry as _node_registry  # noqa: F401

PAUSE = actions_page.PAUSE_PATH
RESTART = "/actions/server/restart"
STARTUP = "/actions/server/startup"
POPUP = actions_page.POPUP_PATH
NODE_RESTART = "/actions/node/restart"
CONFIRM = actions_page.CONFIRM_SUFFIX
ORIGIN = actions_page.ORIGIN_FIELD

DASHBOARD = dashboard_page.DASHBOARD_PATH
SERVERS = servers_page.SERVERS_PATH
PLAYERS = players_page.PLAYERS_PATH
NODES = nodes_page.NODES_PATH


# ---------------------------------------------------------------------------------- helpers

def form_for(html: str, path: str) -> str:
    """The rendered ``<form>`` that posts to *path* — the whole POST shape the browser would send."""
    match = re.search(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert match, f"the page rendered no control posting to {path}"
    return match.group(0)


def fields_of(form: str) -> dict[str, str]:
    """Every hidden/typed field of a rendered form, as the browser would submit it."""
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


def origin_of(html: str, path: str) -> str:
    """The origin a control for *path* carries on the rendered page."""
    fields = fields_of(form_for(html, path))
    assert ORIGIN in fields, f"the control for {path} carries no origin field"
    return fields[ORIGIN]


def confirm_token(html: str) -> str:
    match = re.search(r'name="_confirm_token" value="([^"]+)"', html)
    assert match, "the dialog carries no confirm token"
    return match.group(1)


def back_of(html: str, page_path: str) -> str:
    """Where the dialog's Cancel control (and its ``Esc``) goes back to, RESOLVED against the
    dialog's own URL — the href is a relative reference, so a reverse-proxy prefix survives.

    Cancel is an ANCHOR (M12): a ``<form method="get">`` would replace the action's query with its own
    (empty) serialised data, dropping the tab.
    """
    match = re.search(r'<a class="btn" href="([^"]*)"', html)
    assert match, "the dialog renders no Cancel link"
    return urljoin(page_path, match.group(1))


# ================================================================ 1. the origin is rendered

@pytest.mark.asyncio
async def test_a_server_control_carries_the_page_it_is_rendered_on(config_dir, clean_registry):
    """The SAME strip on two screens states the page it is on: ``/`` on the Dashboard, ``/fleet``
    on the Standing page. This is the value the route resolves, so it has to start correct."""
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        standing = await open_page(http, SERVERS)
    assert origin_of(dashboard, PAUSE) == DASHBOARD
    assert origin_of(standing, PAUSE) == SERVERS


@pytest.mark.asyncio
async def test_a_refreshed_dashboard_fragment_keeps_the_dashboard_as_the_origin(config_dir,
                                                                               clean_registry):
    """The live stream re-renders the SAME table through its own ``/api`` request, so the origin
    cannot be read off the request path — it is the page the fragment belongs to. A refreshed
    control that re-pointed at ``/fleet`` would undo the fix about two seconds after the page
    loaded, which is exactly the failure this pins."""
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        fragments = await http.get(f"{live_page.DASHBOARD_FRAGMENTS_PATH}?level=all")
        assert fragments.status_code == 200, fragments.text
        state = fragments.json()[live_page.STATE_TARGET]
    assert origin_of(state, PAUSE) == DASHBOARD


@pytest.mark.asyncio
async def test_a_player_control_carries_the_page_it_is_rendered_on(config_dir, clean_registry):
    """The player strip lives on the dashboard's Players tab; the directory page renders none.

    The strip's origin is the page it is drawn on, so the one control that exists states ``/`` —
    and the database directory that replaced the old roster page answers with no control at all
    (its rows' name is the door to the player's page, where the writes live).
    """
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD + "?tab=players")
        directory = await open_page(http, PLAYERS)
    assert origin_of(dashboard, POPUP + CONFIRM) == DASHBOARD
    assert POPUP + CONFIRM not in directory, "the directory page must render no player control"


@pytest.mark.asyncio
async def test_a_node_control_carries_the_page_it_is_rendered_on(config_dir, clean_node_registry):
    """The node strip is on the dashboard's Nodes tab and on ``/nodes`` — same rule again."""
    app = build(config_dir, node_fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dashboard = await open_page(http, DASHBOARD + "?tab=nodes")
        standing = await open_page(http, NODES)
    assert origin_of(dashboard, NODE_RESTART + CONFIRM) == DASHBOARD
    assert origin_of(standing, NODE_RESTART + CONFIRM) == NODES


# ============================================================ 2. the route returns to it

@pytest.mark.asyncio
async def test_a_server_write_pressed_on_the_dashboard_returns_to_the_dashboard(
        config_dir, clean_registry, recording_bot):
    """Defect 1's headline: a Startup/Stop pressed on ``/`` must land back on ``/``, not ``/fleet``.

    BEFORE the fix this asserted ``/fleet`` (the section's own page) and reds out.
    """
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        body = fields_of(form_for(dashboard, PAUSE))
        reply = await http.post(PAUSE, data=body)
    assert reply.status_code == 303
    assert reply.headers["location"] == DASHBOARD, \
        "a write pressed on the Dashboard must keep the person on the Dashboard"


@pytest.mark.asyncio
async def test_a_server_write_pressed_on_the_servers_page_returns_to_the_servers_page(
        config_dir, clean_registry, recording_bot):
    """The other side of the same rule: the Standing page's own control still returns there."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        standing = await open_page(http, SERVERS)
        reply = await http.post(PAUSE, data=fields_of(form_for(standing, PAUSE)))
    assert reply.status_code == 303
    assert reply.headers["location"] == SERVERS


@pytest.mark.asyncio
async def test_a_confirm_write_carries_the_origin_through_its_dialog_to_the_dashboard(
        config_dir, clean_registry, recording_bot):
    """A DESTRUCTIVE write: the row posts to the dialog, the dialog re-emits the origin, the action's route
    returns there."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        csrf = token_of(dashboard)

 # the row's control posts to the dialog with the target AND the origin
        row = fields_of(form_for(dashboard, RESTART + CONFIRM))
        assert row[ORIGIN] == DASHBOARD
        dialog = await http.post(RESTART + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text

 # the dialog re-emits BOTH, and its Cancel/Esc go back to the same page
        assert origin_of(dialog.text, RESTART) == DASHBOARD
        assert back_of(dialog.text, RESTART + CONFIRM) == DASHBOARD

        accepted = await http.post(RESTART, data={"server": "SRS-1", "_csrf_token": csrf,
                                                  ORIGIN: DASHBOARD,
                                                  "_confirm_token": confirm_token(dialog.text)})
    assert accepted.status_code == 303
    assert accepted.headers["location"] == DASHBOARD
    assert cluster.servers[0].calls == ["stop", "start"], "the confirmed action ran"


@pytest.mark.asyncio
async def test_a_player_write_returns_to_the_page_it_was_pressed_on(config_dir, clean_registry,
                                                                   recording_bot):
    """The player surface, through its dialog: a message sent from the Dashboard's Players tab
    returns to that TAB — the dashboard's tables share ``/`` and the tab is a query parameter, so
    ``/`` alone would land the person on the default tab (Servers)."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD + "?tab=players")
        row = fields_of(form_for(dashboard, POPUP + CONFIRM))
        assert row[ORIGIN] == DASHBOARD
        assert row[actions_page.TAB_FIELD] == "players"
        dialog = await http.post(POPUP + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
        assert origin_of(dialog.text, POPUP) == DASHBOARD
        reply = await http.post(POPUP, data={**row, "message": "hi",
                                             "_confirm_token": confirm_token(dialog.text)})
    assert reply.status_code == 303
    assert reply.headers["location"] == DASHBOARD + "?tab=players"


@pytest.mark.asyncio
async def test_a_node_write_returns_to_the_page_it_was_pressed_on(config_dir, clean_node_registry,
                                                                 recording_bot):
    """The node surface, through its dialog: a node Restart from the Dashboard's Nodes tab returns
    to that TAB (see the player twin above for why the tab has to ride along)."""
    cluster = node_fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dashboard = await open_page(http, DASHBOARD + "?tab=nodes")
        row = fields_of(form_for(dashboard, NODE_RESTART + CONFIRM))
        assert row[ORIGIN] == DASHBOARD
        assert row[actions_page.TAB_FIELD] == "nodes"
        dialog = await http.post(NODE_RESTART + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
        assert origin_of(dialog.text, NODE_RESTART) == DASHBOARD
        accepted = await http.post(NODE_RESTART,
                                   data={**row, "_confirm_token": confirm_token(dialog.text)})
    assert accepted.status_code == 303
    assert accepted.headers["location"] == DASHBOARD + "?tab=nodes"
    assert cluster.nodes["dcs-a-01"].calls == ["restart"]


# ====================================================== 3. the origin is an allow-list

@pytest.mark.asyncio
@pytest.mark.parametrize("crafted", ["https://evil.example", "//evil.example", "/not-a-page",
                                     "javascript:alert(1)", "", None,
 # the crafted shapes driven live against the real
 # ASGI app; pinned here so a refactor of origin_path cannot drop
 # the guarantee:
                                     "/fleet\r\nLocation: https://evil.example",  # CR/LF injection
                                     "/players?tab=x",                              # a query string
                                     "/\\evil",                                     # backslash before the host
                                     "%2F%2Fevil.example",                          # percent-encoded //
                                     "/players#frag"])                              # a fragment
async def test_a_crafted_origin_falls_back_to_the_section_default_and_never_redirects_away(
        config_dir, clean_registry, recording_bot, crafted):
    """THE safety line: the browser's destination is never raw request input.

    Every value the registry does not return — an absolute URL, a scheme-relative one, a page this
    console does not serve, a query string, a fragment, a CR/LF injection or no value at all —
    answers the section default (``/fleet`` for a server write). A redirect to one of these would
    be an open redirect.
    """
    cluster = running_fleet()
    app = build(config_dir, cluster)
    data = {"server": "SRS-1", "_csrf_token": "x"}
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, DASHBOARD)
        data["_csrf_token"] = token_of(page)
        if crafted is not None:
            data[ORIGIN] = crafted
        reply = await http.post(PAUSE, data=data)
    assert reply.status_code == 303
    assert reply.headers["location"] == SERVERS, "a crafted origin must answer the section default"
 # and NOTHING of the crafted value leaks into the destination the browser will follow
    location = reply.headers["location"]
    assert "evil" not in location, "no off-origin host may reach the browser"
    assert "\r" not in location and "\n" not in location, "header injection must not survive"


@pytest.mark.asyncio
async def test_a_known_internal_page_is_accepted_because_the_registry_answers_it(
        config_dir, clean_registry, recording_bot):
    """The allow-list is the registry, not a per-surface constant: a page this console serves is a
    legal destination (the value RETURNED is the registry's own constant)."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, DASHBOARD)
        reply = await http.post(PAUSE, data={"server": "SRS-1", "_csrf_token": token_of(page),
                                             ORIGIN: "/nodes"})
    assert reply.status_code == 303
    assert reply.headers["location"] == "/nodes"


# ============================================= 4. the outcome is shown where the person lands

@pytest.mark.asyncio
async def test_the_dashboard_shows_the_one_shot_notice_of_a_write_pressed_on_it(
        config_dir, clean_registry, recording_bot):
    """Keeping the person on the Dashboard is only right if the OUTCOME is shown there too: else the
    notice would sit in the session and pop on whatever page they opened next."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        reply = await http.post(PAUSE, data=fields_of(form_for(dashboard, PAUSE)))
        assert reply.status_code == 303
        after = await open_page(http, DASHBOARD)
        assert "paused" in after
        assert 'class="notice' in after


# ================================================= 5. the dialog does not hang (Defect 2)

@pytest.mark.asyncio
async def test_the_confirm_dialog_submits_in_the_background(config_dir, clean_registry,
                                                            recording_bot):
    """Defect 2: the destructive button POSTs with ``fetch`` in the background and hands the page over
    once the write answers, so the browser is never held on a page that cannot say whether the write
    landed. BEFORE the fix the page shipped no interceptor and this reds out on the missing ``fetch``.

    moved the interceptor OUT of this page into the console's ONE copy (``static/submit.js``,
    loaded by ``base.html`` on every page) so the row strip submits in the background too. The
    behaviour is unchanged and is asserted the same way — the request, the refusal line, exactly one
    ``fetch`` — but it is read from the asset the page loads rather than from the page's own inline
    block, and the page is pinned to the attributes that opt its form into it.
    """
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        dialog = await http.post(RESTART + CONFIRM, data=fields_of(form_for(dashboard,
                                                                            RESTART + CONFIRM)))
        script = await http.get("/static/submit.js")
    html = dialog.text
    assert dialog.status_code == 200
 # the page loads the ONE interceptor, and its destructive form opts in
    assert script.status_code == 200, "the shared interceptor must be served"
    assert "/static/submit.js" in html
    form = form_for(html, RESTART)
    assert "data-async" in form, "the destructive form must be a background write"
    assert back_target(form, RESTART + CONFIRM) == DASHBOARD, \
        "the dialog must state the page it returns to"
 #: the dialog WAITS for nothing and shows no refusal line of its own — that wait is the
 # the frozen page. A refusal rides the one-shot notice on the page they land on.
    assert "data-notice" not in form, "the dialog must not wait on a notice line ()"
    assert 'id="dlg-notice"' not in html, "the dialog's own refusal line is gone ()"
 # the interceptor itself: same form, same body, no wait
    interceptor = script.text
    assert "event.preventDefault()" in interceptor
    assert "new FormData(form)" in interceptor
    assert 'redirect: "manual"' in interceptor, "fetch must not follow the 303 itself"
    assert "window.location.assign(to)" in interceptor, "an accepted write must leave the page"
    assert "keepalive: true" in interceptor, \
        "an abandoned write still finishes server-side (the wait, not keepalive, holds it open)"
 # EXACTLY ONE fetch: the one-shot confirm token is never re-sent by a retry
    assert interceptor.count("fetch(") == 1


@pytest.mark.asyncio
async def test_the_dialog_is_still_a_plain_form_the_browser_posts_without_javascript(
        config_dir, clean_registry, recording_bot):
    """The enhancement's fallback: with JavaScript off the form is a real POST to the action's route,
    carries the CSRF field and the one-shot token, and the markup is complete — the behaviour the
    card keeps as the no-JS path."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        dialog = await http.post(RESTART + CONFIRM, data=fields_of(form_for(dashboard,
                                                                            RESTART + CONFIRM)))
    html = dialog.text
    form = form_for(html, RESTART)
    assert 'method="post"' in form
    assert 'name="_csrf_token"' in form, "the no-JS POST needs the session token"
    assert 'name="_confirm_token"' in form, "the no-JS POST needs the one-shot token"
    assert f'name="{ORIGIN}" value="{DASHBOARD}"' in form
 # and the no-JS page is complete, not a script-only shell
    assert 'role="dialog"' in html and 'autofocus' in html
