"""The pause pilot: the console's first visible write control.

The smallest, least risky action of the map, and the one that proves the seam end to end BEFORE any
destructive action exists. One section per acceptance criterion: the DISCOVERY WIRING
(``action_available("pause_mission")`` must be True through the web shell's own process, or every
control is silently omitted while the suite stays green); the round trip; the scope (a manager acting on
somebody else's server is refused with NO disclosure and nothing mutates); the omitted control; the
action's OWN guards (not running, no mission) surfacing inline; the audit (the entry names the WEB actor
and a refused attempt does not claim success); and CSRF. Driven through the real app with the real local
login backend as the identity and a stub cluster as the source; the domain boundary is the ``Mission``
double."""
from __future__ import annotations

import re
import types

import httpx
import pytest

from conftest import asgi_client, create_app, install_shell

from core import Status
import core.actions as actions_module
from core.actions import action_available, action_registry, reset_in_flight
from services.webservice import readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

#: every role name an account below declares must be a KNOWN role: the local backend validates the
#: declared roles at startup and refuses the whole install on an unknown one.
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

#: the six identities as REAL local accounts: the roles and the scope are the
#: account's declaration, and ``AuthManager`` derives the manager fact from it.
USERS = (
    {"username": "specialk", "roles": ["Admin"]},                          # unscoped by rule
    {"username": "mod1", "roles": ["DCS Admin"], "scope": ["Host A"]},     # DCS Admin, in scope
    {"username": "hoster", "roles": ["DCS"], "scope": ["Host A"]},         # a pure MANAGER
    {"username": "hoster-b", "roles": ["DCS"], "scope": ["Host B"]},       # manager, other server
    {"username": "plain", "roles": ["DCS"]},                              # no capability at all
)


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _users():
    return [dict(user, password_hash=_hash()) for user in USERS]


def _config(users=None) -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users if users is not None else _users()}},
    }


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def clean_registry():
    """The ``@action`` registry and the in-flight guard are PROCESS state — restore both.

    Cleared BEFORE the test so the discovery assertion cannot pass on a registry an earlier test
    populated: the shell's own install has to have done the work.

    ``plugins.mission.actions`` is dropped from the MODULE CACHE as well, and that is what makes the
    pin honest: ``discover_actions`` populates the registry through the decorators a module runs
    ON IMPORT, which happens once per process. Without this, the FIRST test would see a genuine
    discovery and every later one would silently observe an empty registry (the import is a no-op
    the second time) — a green suite over a control nobody renders.
    """
    import sys

    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    reset_in_flight()
    sys.modules.pop("plugins.mission.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        reset_in_flight()
        sys.modules.pop("plugins.mission.actions", None)


# ---------------------------------------------------------------------------------- test doubles

class FakeMission:
    """The ``Mission`` double: pause/unpause are awaitable and FLIP THE SERVER'S STATE.

    Flipping the status is the double's job, not the console's: in production the DCS broadcast
    (``plugins/mission/listener.py``) sets ``Status.PAUSED``. A test that left the status alone
    could not tell "the page reflects the new state" from "the page never changed".
    """

    def __init__(self):
        self.server = None
        self.paused = False
        self.calls: list[str] = []

    async def pause(self):
        self.calls.append("pause")
        self.paused = True
        if self.server is not None:
            self.server.status = Status.PAUSED

    async def unpause(self):
        self.calls.append("unpause")
        self.paused = False
        if self.server is not None:
            self.server.status = Status.RUNNING


def server(name, managed_by=None, *, status=Status.RUNNING, node_name="dcs-a-01",
           instance="Host-A-Instance"):
    """A server double shaped like ``ServerImpl`` where the scope, the read models and the actions read
    it: ``locals`` carries ``managed_by`` (``None`` = no restriction), and ``current_mission`` is what
    ``pause_mission`` calls."""
    mission = FakeMission()
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance), port=10309,
        current_mission=mission, players={}, is_remote=False,
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    mission.server = srv
    return srv


def instance(name, server_obj=None):
    return types.SimpleNamespace(name=name, dcs_port=10309, webgui_port=8088, server=server_obj)


class Cluster:
    """A stub cluster behind the app's provider seam (``app.state.webui_source_provider``)."""

    def __init__(self, servers=(), *, nodes=None, node=None, instances=()):
        self.servers = list(servers)
        self.instances = list(instances)
        self.nodes = dict(nodes or {})
        self.node = node

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=tuple(self.instances),
                                     nodes=self.nodes, node=self.node, log_path=None)


def fleet():
    """A (hosted by ``Host A``) and B (hosted by ``Host B``) — the scope's two sides."""
    a = server("SRS-1", ["Host A"])
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a},
                   node=node_a, instances=[instance("Host-A-Instance", a),
                                           instance("Host-B-Instance", b)])


class RecordingBot:
    """A bot whose ``audit`` records what it was asked to write — the audit's domain boundary."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))


@pytest.fixture
def recording_bot():
    """Install a bot that CAN confirm an audit entry, for the process's whole request handling."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    bot = RecordingBot()
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        yield bot
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


# ---------------------------------------------------------------------------------- the harness

def build(config_dir, cluster, *, plugins=("mission",), users=None):
    """A real application: the production factory, the shell, the local backend and a stub source."""
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir),
                                                  plugins=[p for p in plugins]),
                  config=_config(users))
    app.state.webui_source_provider = cluster.source
    return app


def client(app):
    """The suite's ASGI client; the 303 is NOT followed, so a test can assert it and re-read."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://testserver")


async def sign_in(http, username="mod1"):
    response = await http.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    reply = await http.post(auth_routes.LOGIN_PATH,
                            data={"username": username, "password": PASSWORD,
                                  "_csrf_token": token})
    assert reply.status_code in (200, 303), f"login for {username} failed: {reply.status_code}"
    return reply


def token_of(html: str) -> str:
    """The session's CSRF token, read out of a RENDERED form (never minted by the test)."""
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


async def open_page(http, path=servers_page.SERVERS_PATH):
    response = await http.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    return response.text


async def act(http, path, name):
    """POST one control the page rendered: the session's token, the target in the BODY."""
    html = await open_page(http)
    assert f'action="{path}"' in html, f"the page offered no control for {path}"
    return await http.post(path, data={"server": name, "_csrf_token": token_of(html)})


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_pause_action_in_its_own_process(config_dir, clean_registry):
    """ACCEPTANCE 1 — ``action_available("pause_mission")`` is True through the shell's install.

    The ONLY other ``discover_actions`` caller is ``services/mcpservice/server.py`` (a different
    service, a different lifecycle), so without the wiring in ``install_shell`` the registry stays
    EMPTY here, every control is silently omitted and the whole suite stays green. This test is
    what stops the pilot being dead code.
    """
    assert action_available("pause_mission") is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet())
    assert action_available("pause_mission") is True
    assert action_available("unpause_mission") is True
 # the registry is the plugin's, not a hand-written entry
    assert "pause_mission" in action_registry()


@pytest.mark.asyncio
async def test_the_shell_install_of_a_node_without_the_mission_plugin_finds_no_action(config_dir,
                                                                                      clean_registry):
    """The CONTROL for the test above: with no plugin there is nothing to discover.

    A discovery assertion that would pass for any input is not an assertion — the empty-plugin-list
    install must leave the registry empty, so the pin above is measuring the wiring.
    """
    build(config_dir, fleet(), plugins=())
    assert action_available("pause_mission") is False


# ------------------------------------------------------------- 2./3. the round trip, on the page

@pytest.mark.asyncio
async def test_a_dcs_admin_pauses_and_unpauses_a_mission_in_their_scope(config_dir, clean_registry,
                                                                       recording_bot):
    """ACCEPTANCE 2 + 3 — the round trip the pilot exists to prove."""
    cluster = fleet()
    app = build(config_dir, cluster)
    mission = cluster.servers[0].current_mission

    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)

 # the control is offered, in the console's own visual language: a form, in the row's
 # Actions cell, with the session's token and the target in the BODY (never in a URL)
        assert 'action="/actions/server/pause"' in html
        assert 'action="/actions/server/unpause"' not in html, "a RUNNING server offers Pause only"
        assert "?server=" not in html, "a target must never travel in a URL"
        assert token_of(html)

        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(html)})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == servers_page.SERVERS_PATH
        assert mission.calls == ["pause"] and mission.paused is True

 # the page re-reads: the notice says what happened and the row shows the PAUSED state
        after = await open_page(http)
        assert "Mission on server &#39;SRS-1&#39; paused." in after or \
               "Mission on server 'SRS-1' paused." in after
        assert 'action="/actions/server/unpause"' in after, "a PAUSED server offers Unpause"
        assert 'action="/actions/server/pause"' not in after
 # the server row's controls are an ICON STRIP now, so the label is the
 # control's ACCESSIBLE NAME (aria-label) and the sentence the native tooltip carries, not the
 # button's text. The old assertion (`">Unpause<" in after`) pinned the visible word, which
 # the approved mockup replaced with a glyph — the control itself is asserted the same way it
 # is now offered, and the glyph is pinned by tests/test_webui_write_server_actions.py.
        assert 'title="Unpause — resume the frozen mission"' in after
        assert 'aria-label="Unpause the mission on SRS-1"' in after

 # THE ROUND TRIP: the same identity unpauses it, through the control next to it
        reply = await http.post(actions_page.UNPAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(after)})
        assert reply.status_code == 303
        assert mission.calls == ["pause", "unpause"] and mission.paused is False

        final = await open_page(http)
        assert "unpaused" in final
        assert 'action="/actions/server/pause"' in final

 # the audit, once per ATTEMPT, naming the WEB actor and the server
    assert [call[0] for call in recording_bot.calls] == [
        "Mission on server 'SRS-1' paused.",
        "Mission on server 'SRS-1' unpaused.",
    ]
    assert recording_bot.calls[0][1]["server"] is cluster.servers[0]


@pytest.mark.asyncio
async def test_the_pause_route_is_a_post_and_never_a_get(config_dir, clean_registry):
    """A state change is a POST: the route does not answer a GET at all."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(actions_page.PAUSE_PATH)
    assert response.status_code == 405


# --------------------------------------------------------------------------- 4. the scope

@pytest.mark.asyncio
async def test_a_manager_is_refused_on_a_server_outside_their_scope(config_dir, clean_registry,
                                                                    recording_bot):
    """ACCEPTANCE 4 — the refusal discloses nothing and nothing mutates."""
    cluster = fleet()
    app = build(config_dir, cluster)
    b = cluster.servers[1]
    assert b.current_mission.calls == []

    async with client(app) as http:
        await sign_in(http, "hoster-b")            # manages Host B
        page = await open_page(http)
        assert "Host B Server" not in page and "SRS-B" in page, "the scoped view keeps their own row"
        assert 'value="SRS-1"' not in page, "no control on somebody else's row"

 # a crafted POST naming the other hoster's server
        reply = await http.post(actions_page.PAUSE_PATH,
                               data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 403
        assert "SRS-1" not in reply.text, "the refusal must not disclose the target"

 #... and the same for a name that does not exist: SAME answer, no enumeration
        missing = await http.post(actions_page.PAUSE_PATH,
                                  data={"server": "NOPE-9", "_csrf_token": token_of(page)})
        assert missing.status_code == 403
        assert missing.text == reply.text

    assert cluster.servers[0].current_mission.calls == [], "nothing may run for a refused caller"
 # a scope refusal is the ROUTE's answer and happens before any
 # action, so it carries no ActionResult and is not part of this card's audit step — the trail
 # records the action's own refusals instead (see test_pausing_a_server_that_is_not_running).
    assert recording_bot.calls == [], "a refused request never reached an action"


@pytest.mark.asyncio
async def test_a_manager_pauses_a_mission_on_their_own_server(config_dir, clean_registry,
                                                            recording_bot):
    """``scope_grants`` is what makes this true: ``hoster`` holds NO console role (``DCS`` only) and
    reaches the write through the SCOPE alone."""
    cluster = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await act(http, actions_page.PAUSE_PATH, "SRS-1")
    assert reply.status_code == 303
    assert cluster.servers[0].current_mission.paused is True
    assert recording_bot.calls[0][0] == "Mission on server 'SRS-1' paused."


# ------------------------------------------------------- 5. an identity without the capability

@pytest.mark.asyncio
async def test_an_identity_without_the_capability_is_offered_no_control_and_refused(
        config_dir, clean_registry, recording_bot):
    """ACCEPTANCE 5 — no control in any markup, and a hand-crafted POST is a refusal, not a 500."""
    cluster = fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "plain")               # ``DCS`` only: not a console reader
        page = await http.get(servers_page.SERVERS_PATH)
        assert page.status_code == 403
        assert 'action="/actions/server/pause"' not in page.text
        assert ">Pause<" not in page.text

        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"
        assert "500" not in reply.text and "Internal Server Error" not in reply.text

    assert cluster.servers[0].current_mission.calls == []
    assert recording_bot.calls == []


@pytest.mark.asyncio
async def test_an_unavailable_action_is_omitted_and_a_crafted_post_is_a_typed_refusal(
        config_dir, clean_registry, recording_bot):
    """The action layer absent (the plugin is not loaded): the control is OMITTED, and a crafted
    POST gets the seam's typed "not available" answer inline — never a 500, never a fake success."""
    cluster = fleet()
    app = build(config_dir, cluster)
    actions_module._ACTIONS.clear()      # the ``mission`` plugin is not on this install

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        assert 'action="/actions/server/pause"' not in page

        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 303, "a typed refusal is a rendered banner, not an error status"
        after = await open_page(http)
        assert "not available" in after

    assert cluster.servers[0].current_mission.calls == []


# ------------------------------------------------------------------ 6. the action's own guards

@pytest.mark.asyncio
async def test_pausing_a_server_that_is_not_running_surfaces_the_typed_refusal(config_dir,
                                                                              clean_registry,
                                                                              recording_bot):
    """ACCEPTANCE 6a — the action's own state guard, rendered inline (and no control is offered for
    a state the action would refuse)."""
    cluster = fleet()
    cluster.servers[0].status = Status.STOPPED
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        assert 'action="/actions/server/pause"' not in page, "a Stopped row is offered no Pause"

        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 303
        after = await open_page(http)
        assert "is Stopped, not running." in after

    assert cluster.servers[0].current_mission.calls == []
 # the refusal is audited too -- one entry per attempt -- and it claims no success
    assert recording_bot.calls[0][0] == ("Server 'SRS-1' is Stopped, not running.")


@pytest.mark.asyncio
async def test_pausing_with_no_mission_surfaces_the_typed_refusal(config_dir, clean_registry,
                                                                 recording_bot):
    """ACCEPTANCE 6b — the second guard the action already has."""
    cluster = fleet()
    cluster.servers[0].current_mission = None
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert reply.status_code == 303
        after = await open_page(http)
        assert "No active mission on server &#39;SRS-1&#39;." in after or \
               "No active mission on server 'SRS-1'." in after
    assert recording_bot.calls[0][0] == "No active mission on server 'SRS-1'."


@pytest.mark.asyncio
async def test_the_seam_warns_when_nobody_audits_the_result(config_dir, clean_registry, caplog):
    """A caller that audits NOTHING still gets the warning.

    ``audit_result`` is opt-in, so the seam's "this write has no trail" signal survives for every other
    caller -- the flag buys the pilot's trail, it does not silence the check.
    """
    import logging

    from core.actions import ActionContext, call_action, action

    @action
    async def unaudited_probe(ctx, server_name):
        from core.action_results import ActionResult

        return ActionResult(success=True, message="did something")

    bus = types.SimpleNamespace(servers={})
    ctx = ActionContext(node=types.SimpleNamespace(name="NODE"), bus=bus)
    with caplog.at_level(logging.WARNING):
        result = await call_action(unaudited_probe.__qualname__, ctx, server_name="SRS-1")
    assert result.success is True
    assert "no audit entry" in caplog.text


@pytest.mark.asyncio
async def test_the_route_audits_through_the_seam_and_never_logs_a_missing_trail(config_dir,
                                                                              clean_registry,
                                                                              recording_bot, caplog):
    """The pilot's audit is written ONCE, by the seam, and the log does NOT claim it is missing.

    This is the observable defect the seam option closes: with the route auditing after the fact,
    every pause logged "the operation has no trail" about a write that was audited — a warning that
    is false on the one operation it is about.
    """
    import logging

    cluster = fleet()
    app = build(config_dir, cluster)
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        async with client(app) as http:
            await sign_in(http, "mod1")
            reply = await act(http, actions_page.PAUSE_PATH, "SRS-1")
            assert reply.status_code == 303
            after = await open_page(http)
    assert "no audit entry" not in caplog.text
    assert "could not be recorded" not in after, "the seam confirmed the entry"
    assert len(recording_bot.calls) == 1
    assert recording_bot.calls[0][0] == "Mission on server 'SRS-1' paused."
    assert recording_bot.calls[0][1]["server"] is cluster.servers[0], \
        "the trail is written against the RESOLVED server, not a bare name"


# ------------------------------------------------------------------------------- 7. the audit

@pytest.mark.asyncio
async def test_a_failed_audit_is_reported_on_the_page_without_failing_the_write(config_dir,
                                                                               clean_registry):
    """ACCEPTANCE 7b — a success whose trail could not be written says so, and stays a success."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    cluster = fleet()
    app = build(config_dir, cluster)

    class FailingBot(RecordingBot):
        async def audit(self, message, **kwargs):
            raise RuntimeError("the audit channel is unreachable")

    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=FailingBot())
    try:
        async with client(app) as http:
            await sign_in(http, "mod1")
            reply = await act(http, actions_page.PAUSE_PATH, "SRS-1")
            assert reply.status_code == 303
            after = await open_page(http)
            assert "paused" in after
            assert "could not be recorded" in after, "a lost audit entry is never a green banner"
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


@pytest.mark.asyncio
async def test_no_bot_in_the_process_is_reported_as_not_recorded(config_dir, clean_registry):
    """A headless/absent bot cannot confirm an entry, so the page says so."""
    cluster = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        reply = await act(http, actions_page.PAUSE_PATH, "SRS-1")
        assert reply.status_code == 303
        after = await open_page(http)
        assert "could not be recorded" in after


# --------------------------------------------------------------------------------- 8. the CSRF

@pytest.mark.asyncio
async def test_a_post_without_a_valid_csrf_token_is_refused_and_nothing_runs(config_dir,
                                                                           clean_registry,
                                                                           recording_bot):
    """A write carries the SESSION's token: absent, or another session's, is a refusal.

    The two faces, on the wire: a BROWSER (``Accept: text/html``) gets the refusal page whose copy
    answers the only question the author of a refused write has — "was my work saved?" — and a
    non-browser caller keeps the JSON it has always read.
    """
    cluster = fleet()
    app = build(config_dir, cluster)
    browser = {"Accept": "text/html"}

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)

        no_token = await http.post(actions_page.PAUSE_PATH, data={"server": "SRS-1"},
                                   headers=browser)
        assert no_token.status_code == 403
        assert "nothing was saved" in no_token.text
        assert "SRS-1" not in no_token.text

        foreign = await http.post(actions_page.PAUSE_PATH,
                                  data={"server": "SRS-1", "_csrf_token": "not-this-session"},
                                  headers=browser)
        assert foreign.status_code == 403
        assert "nothing was saved" in foreign.text

 # the API face is unchanged: the same status, a machine-readable body
        api = await http.post(actions_page.PAUSE_PATH,
                              data={"server": "SRS-1", "_csrf_token": "not-this-session"})
        assert api.status_code == 403
        assert api.json()["detail"] == "CSRF token missing or invalid."

    assert cluster.servers[0].current_mission.calls == []
    assert recording_bot.calls == []


# ----------------------------------------------------- the six identities, at a glance (matrix)

@pytest.mark.asyncio
@pytest.mark.parametrize("who, sees_control, post_status", [
    ("specialk", True, 303),      # Admin: unscoped by rule, wider than Discord on purpose
    ("mod1", True, 303),          # DCS Admin, server in scope
    ("hoster", True, 303),        # a pure MANAGER, in scope
    ("hoster-b", False, 403),     # a manager of another server: no control, bare refusal
    ("plain", False, 403),        # no capability at all
])
async def test_the_identity_matrix_of_the_control_and_the_route(config_dir, clean_registry,
                                                                recording_bot, who, sees_control,
                                                                post_status):
    """Every identity the report names, on one surface: what it SEES and what it can DO.

    ``anonymous`` is the sixth and is tested by:func:`test_an_anonymous_post_is_refused`.
    """
    cluster = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, who)
        page = await http.get(servers_page.SERVERS_PATH)
 # the form tag may carry the background-submit opt-in (: `data-async` on a strip
 # control), so the shape is pinned by the ACTION it posts to and its body, not by an exact tag
        offer = re.search(r'<form method="post" action="/actions/server/pause"[^>]*>'
                          r'<input type="hidden" name="_csrf_token" value="[^"]*">'
                          r'<input type="hidden" name="server" value="SRS-1">', page.text)
        offered = offer is not None
        if sees_control:
            assert page.status_code == 200, f"{who} should read the page"
        assert offered is sees_control, f"{who}: control for SRS-1 offered={offered}"
        token = token_of(page.text) if page.status_code == 200 else "t" * 43
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token})
        assert reply.status_code == post_status, f"{who}: {reply.status_code}"


@pytest.mark.asyncio
async def test_an_anonymous_post_is_refused(config_dir, clean_registry, recording_bot):
    """The sixth identity: no session at all. The refusal is the console's, and nothing runs."""
    cluster = fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": "t" * 43})
        assert reply.status_code == 403
    assert cluster.servers[0].current_mission.calls == []
    assert recording_bot.calls == []
