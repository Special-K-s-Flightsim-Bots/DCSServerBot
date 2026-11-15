"""The apply door: after a save, ONE control that reloads the plugin or restarts the bot it needs.

The mapping is the card's, and it is derived SERVER-SIDE from the file that was just written —
never from a posted plugin or node name. These pins therefore drive the REAL application: a save,
the notice it leaves, the confirmation it opens, the refusals, and the ordering rule that the work
runs only after the answer is on the wire.

Nothing here is asserted through a rendered template alone where an HTTP request can prove it: the
control is a form the page really renders, and the door really refuses a GET, a caller without the
capability, a spent token and a file the offer has outrun.
"""
from __future__ import annotations

import re
import types

import pytest

from services.webservice import config_apply
from services.webservice.pages import servers as servers_page
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, instance, server, sign_in,
)

ADMIN = "specialk"
MASTER = "dcs-master-01"
NODE_A = "dcs-a-01"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

MAIN_YAML = """\
guild_id: 112233445566
guild_name: My Server
language: en
plugins:
  - mission
"""

NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Host-A-Instance:
      home: /tmp/a
      bot_port: 6666
      webgui_port: 8088
      dcs_port: 10308
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Host-B-Instance:
      home: /tmp/b
      bot_port: 6667
      webgui_port: 8089
      dcs_port: 10309
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
SRS-B:
  node: dcs-b-01
"""

SCHEDULER_YAML = """\
# the install's scheduler configuration
DEFAULT:
  startup_delay: 10
  timezone: UTC
Host-A-Instance:
  timezone: Europe/Berlin
Host-B-Instance:
  timezone: America/New_York
"""

#: the node page's Expert Mode holds ONE node's SLICE, so its top-level key is the node itself.
NODE_A_SLICE = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10099
  instances:
    Host-A-Instance:
      home: /tmp/a
      bot_port: 6666
      webgui_port: 8088
      dcs_port: 10308
"""


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8", newline="\n")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "scheduler.yaml").write_text(SCHEDULER_YAML, encoding="utf-8", newline="\n")
    (tmp_path / "main.yaml").write_text(MAIN_YAML, encoding="utf-8", newline="\n")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8", newline="\n")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8", newline="\n")
    return tmp_path


@pytest.fixture(autouse=True)
def node_data(config_dir, monkeypatch):
    """Bind the bot's own validator to THIS install, as production binds it at startup.

    The scheduler schema's root check reads ``nodes.yaml`` through ``validators.NodeData``; without
    this the file's own save refuses on a singleton that names no configuration.
    """
    import core.commandline as commandline
    from core.utils import validators

    saved = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(config_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved


def cluster():
    a = server("SRS-1", ["Host A"])
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name=NODE_A, master=False, instances={})
    node_master = types.SimpleNamespace(name=MASTER, master=True,
                                        instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={MASTER: node_master, NODE_A: node_a, "dcs-b-01": node_a},
                   node=node_master,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


class ApplyBot:
    """The bot double: one reloadable plugin, an audit trail, and nothing else."""

    def __init__(self, loaded=("scheduler",)):
        self.extensions = {f"plugins.{name}.commands": object() for name in loaded}
        self.reloads: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    async def reload_plugin(self, plugin: str) -> bool:
        self.reloads.append(plugin)
        self.extensions[f"plugins.{plugin.lower()}.commands"] = object()
        return True

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))


@pytest.fixture
def apply_bot():
    """Install the bot double in the service registry for the whole request."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    bot = ApplyBot()
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        yield bot
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


@pytest.fixture(autouse=True)
def fresh_apply_state():
    """The in-flight set is PROCESS state: a stale claim would refuse the next test's restart."""
    config_apply.reset_in_flight()
    yield
    config_apply.reset_in_flight()


def app_for(config_dir, monkeypatch, *, plugins=("admin", "scheduler"), master=MASTER):
    monkeypatch.setattr(config_apply, "master_name", lambda: master)
    return build(config_dir, cluster(), plugins=plugins)


def csrf_of(page: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', page)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


def revision_of(page: str) -> str:
    match = re.search(r'name="_revision" value="([^"]*)"', page)
    assert match, "the page rendered no revision field"
    return match.group(1)


async def open_page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}: {response.text[:300]}"
    return response.text


async def save_expert(http, path: str, page_path: str, document: str):
    """Save ONE file through its Expert Mode door — the shortest honest save of a whole document."""
    page = await open_page(http, page_path)
    return await http.post(path, data={"_csrf_token": csrf_of(page), "_revision": revision_of(page),
                                       "_expert": "1", "_yaml": document})


def apply_control(page: str) -> dict:
    """The "Apply now" form a notice rendered, as the fields a browser would post."""
    form = re.search(r'<form class="notice-apply"[^>]*action="([^"]*)"[^>]*>(.*?)</form>', page,
                     re.S)
    assert form, "the notice offered no Apply control"
    fields = dict(re.findall(r'name="([^"]*)" value="([^"]*)"', form.group(2)))
    return {"action": form.group(1), "fields": fields}


async def open_confirmation(http, page: str) -> dict:
    """Press "Apply now" and read the confirmation it opens: its HTML and the token it carries."""
    control = apply_control(page)
    reply = await http.post(control["action"], data=control["fields"])
    assert reply.status_code == 200, reply.text[:400]
    token = re.search(r'name="_confirm_token" value="([^"]*)"', reply.text)
    assert token, "the confirmation rendered no confirm token"
    return {"body": reply.text, "token": token.group(1), "fields": control["fields"]}


def confirm_fields(confirmation: dict, action: str = "apply", **overrides) -> dict:
    return {**confirmation["fields"], "apply_action": action,
            "_confirm_token": confirmation["token"], **overrides}


async def post_apply(http, data: dict):
    return await http.post(settings_page.SETTINGS_APPLY_PATH, data=data)


# ============================================== 1. a plugin save reloads THAT plugin

@pytest.mark.asyncio
async def test_a_plugin_save_offers_a_reload_of_that_plugin(config_dir, monkeypatch,
                                                            clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save_expert(http, "/actions/settings/config/plugin/scheduler",
                                  "/settings/plugin/scheduler",
                                  SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 20"))
        assert reply.status_code == 303, reply.text[:300]
        page = await open_page(http, "/settings/plugin/scheduler")
    assert (config_dir / "plugins" / "scheduler.yaml").read_text(encoding="utf-8").count(
        "startup_delay: 20") == 1
    control = apply_control(page)
    assert control["action"] == settings_page.SETTINGS_APPLY_ASK_PATH
    assert control["fields"]["_apply_kind"] == settings_page.KIND_PLUGIN
    assert control["fields"]["_apply_name"] == "scheduler"
    assert "Apply now" in page


@pytest.mark.asyncio
async def test_the_confirmation_of_a_plugin_names_the_plugin_and_nothing_restarts(
        config_dir, monkeypatch, clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 20"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
    body = confirmation["body"]
    assert "Reload the scheduler plugin now?" in body
    assert "Reload the scheduler plugin" in body
    assert "Nothing restarts and no DCS server goes down." in body
    assert "restart the bot on node" not in body.lower()
    # the console's own confirm-token contract, and the two ways out of ONE form
    assert 'name="_confirm_token"' in body
    assert 'name="apply_action" value="apply"' in body
    assert 'name="apply_action" value="decline"' in body


@pytest.mark.asyncio
async def test_applying_a_plugin_reloads_it_and_says_so(config_dir, monkeypatch, clean_registry,
                                                       apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 20"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert apply_bot.reloads == ["scheduler"]
    # the page the operator lands on ALREADY states the outcome: the reload ran before it rendered
    assert "The scheduler plugin was reloaded, so the change is live." in landed
    assert "Reloading the scheduler plugin" not in landed, "no pending state to wait through"
    assert 'http-equiv="refresh"' not in landed


# ============================================== 2. a main.yaml save restarts the MASTER

@pytest.mark.asyncio
async def test_a_main_yaml_save_offers_a_master_restart(config_dir, monkeypatch, clean_registry,
                                                       apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                                  MAIN_YAML.replace("My Server", "My Renamed Server"))
        assert reply.status_code == 303, reply.text[:300]
        page = await open_page(http, "/settings/main/main")
        control = apply_control(page)
        assert control["fields"]["_apply_kind"] == settings_page.KIND_MAIN
        confirmation = await open_confirmation(http, page)
    body = confirmation["body"]
    assert f"Restart the bot on node {MASTER} now?" in body
    assert f"Every DCS server on {MASTER} goes down with it." in body
    # the button states the operation itself, node named — never the bare word "apply"
    go = re.search(r'value="apply">([^<]*)</button>', body)
    assert go and go.group(1).strip() == f"Restart the bot on node {MASTER}"


# ============================================== 3. a nodes.yaml save names the node it changes

@pytest.mark.asyncio
async def test_a_nodes_yaml_save_names_the_node_it_changes(config_dir, monkeypatch, clean_registry,
                                                          apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http, f"/nodes/{NODE_A}")
        reply = await http.post(f"/nodes/{NODE_A}",
                                data={"_csrf_token": csrf_of(page), "_revision": revision_of(page),
                                      "_expert": "1", "_yaml": NODE_A_SLICE})
        assert reply.status_code == 303, reply.text[:300]
        node_page = await open_page(http, f"/nodes/{NODE_A}")
        control = apply_control(node_page)
        assert control["fields"]["_apply_kind"] == settings_page.KIND_NODES
        confirmation = await open_confirmation(http, node_page)
    body = confirmation["body"]
    assert f"Restart the bot on node {NODE_A} now?" in body
    assert MASTER not in body


# ============================================== 4. the target is the FILE's, never a posted name

@pytest.mark.asyncio
async def test_a_posted_node_cannot_turn_a_plugin_save_into_a_restart(config_dir, monkeypatch,
                                                                     clean_registry, apply_bot):
    """A plugin file reloads its plugin — even when the request names a node it may not use."""
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http, "/settings/plugin/scheduler")
        reply = await http.post(f"/actions/settings/config/plugin/scheduler?node={NODE_A}",
                                data={"_csrf_token": csrf_of(page), "_revision": revision_of(page),
                                      "_expert": "1",
                                      "_yaml": SCHEDULER_YAML.replace("startup_delay: 10",
                                                                      "startup_delay: 30")})
        assert reply.status_code == 303, reply.text[:300]
        after = await open_page(http, "/settings/plugin/scheduler")
    control = apply_control(after)
    assert control["fields"]["_apply_kind"] == settings_page.KIND_PLUGIN
    assert control["fields"]["_apply_name"] == "scheduler"
    assert NODE_A not in control["fields"]["_apply_name"]


@pytest.mark.asyncio
async def test_a_posted_node_cannot_retarget_a_master_restart(config_dir, monkeypatch,
                                                             clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http, "/settings/main/main")
        reply = await http.post(f"/actions/settings/config/main/main?node={NODE_A}",
                                data={"_csrf_token": csrf_of(page), "_revision": revision_of(page),
                                      "_expert": "1",
                                      "_yaml": MAIN_YAML.replace("My Server", "Renamed")})
        assert reply.status_code == 303, reply.text[:300]
        after = await open_page(http, "/settings/main/main")
        confirmation = await open_confirmation(http, after)
    assert f"Restart the bot on node {MASTER} now?" in confirmation["body"]


# ============================================== 5. an unclassifiable file / no change: no control

def test_a_file_outside_the_table_classifies_as_nothing(tmp_path):
    apply = config_apply.resolve_target(tmp_path / "presets.yaml", tmp_path)
    assert apply.kind == config_apply.TARGET_NONE
    assert apply.reason == config_apply.REFUSED_LAUNCH_ONLY
    assert config_apply.resolve_target(tmp_path / "presets" / "night.yaml",
                                       tmp_path).kind == config_apply.TARGET_NONE


@pytest.mark.asyncio
async def test_a_save_that_produced_no_change_offers_no_control(config_dir, monkeypatch,
                                                               clean_registry, apply_bot):
    """The same document saved again writes nothing — and the notice offers no Apply at all."""
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler", SCHEDULER_YAML)
        page = await open_page(http, "/settings/plugin/scheduler")
    assert "Nothing changed" in page
    assert "notice-apply" not in page
    assert "Apply now" not in page


# ============================================== 6. a GET applies nothing

@pytest.mark.asyncio
async def test_a_get_to_the_door_is_refused(config_dir, monkeypatch, clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        for path in (settings_page.SETTINGS_APPLY_PATH, settings_page.SETTINGS_APPLY_ASK_PATH):
            reply = await http.get(path)
            # 405 for the door itself, 404 where the file-page route claims the two segments first:
            # either way the request is REFUSED and nothing is reloaded or restarted.
            assert reply.status_code in (404, 405), f"{path} answered {reply.status_code} to a GET"
    assert apply_bot.reloads == []
    assert config_apply.in_flight(MASTER) is False


# ============================================== 7. a caller without the capability is refused

@pytest.mark.asyncio
async def test_a_non_admin_post_is_refused(config_dir, monkeypatch, clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, "mod1")                       # DCS Admin: may not edit the install
        page = await open_page(http, servers_page.SERVERS_PATH)
        reply = await http.post(settings_page.SETTINGS_APPLY_PATH,
                                data={"_csrf_token": csrf_of(page),
                                      "_apply_kind": settings_page.KIND_PLUGIN,
                                      "_apply_name": "scheduler",
                                      "_confirm_token": "x", "apply_action": "apply"})
    assert reply.status_code == 403
    assert apply_bot.reloads == []


# ============================================== 8. the token

@pytest.mark.asyncio
async def test_an_absent_offer_is_refused_and_nothing_runs(config_dir, monkeypatch, clean_registry,
                                                           apply_bot):
    """A fresh session holds no offer: the door refuses rather than inventing a target."""
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await open_page(http, "/settings")
        reply = await post_apply(http, {"_csrf_token": csrf_of(page),
                                        "_apply_kind": settings_page.KIND_PLUGIN,
                                        "_apply_name": "scheduler",
                                        "_confirm_token": "x", "apply_action": "apply"})
        assert reply.status_code == 303
        landed = await open_page(http, "/settings")
    assert "no longer valid" in landed
    assert apply_bot.reloads == []


@pytest.mark.asyncio
async def test_a_wrong_confirm_token_is_refused(config_dir, monkeypatch, clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 40"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation, _confirm_token="not-the-token"))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert "no longer valid" in landed
    assert apply_bot.reloads == []


# ============================================== 9. declining leaves the "still owed" notice

@pytest.mark.asyncio
async def test_declining_leaves_the_notice_that_names_what_is_still_owed(config_dir, monkeypatch,
                                                                        clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 50"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation, action="decline"))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert "The change is in the file and takes effect after the scheduler plugin is reloaded." in landed
    assert apply_bot.reloads == []


@pytest.mark.asyncio
async def test_declining_a_restart_names_the_node_the_change_owes(config_dir, monkeypatch,
                                                                 clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                          MAIN_YAML.replace("My Server", "Declined Name"))
        page = await open_page(http, "/settings/main/main")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation, action="decline"))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/main/main")
    assert f"takes effect after a bot restart on {MASTER}" in landed


# ============================================== 10. the work runs AFTER the answer is sent

class RecordingApp:
    """An ASGI wrapper that remembers when this app finished SENDING its response body.

    ``sent`` is reset by the test immediately before the POST under measurement: it is one flag, and
    every earlier request of the same session would otherwise leave it already true.
    """

    def __init__(self, app):
        self.app = app
        self.sent = False

    async def __call__(self, scope, receive, send):
        async def wrapped(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.sent = True

        await self.app(scope, receive, wrapped)


@pytest.mark.asyncio
async def test_the_reload_runs_inside_the_answer_so_the_landing_page_is_current(
        config_dir, monkeypatch, clean_registry, apply_bot):
    """A PLUGIN reload is awaited: the page it lands on must read the NEW configuration.

    A restart is the opposite case (see the next test): it ends the process that is answering, so it
    goes to the background. A reload rebuilds one cog in a few milliseconds and the answer survives
    it, so sending the operator to a page that renders the OLD plan — with a "reload this page" link
    for when the background work happens to have finished — is a page that lies about its own state.
    """
    observed: list[bool] = []
    app = app_for(config_dir, monkeypatch)
    recording = RecordingApp(app)

    async def fake_execute(target, ctx):
        observed.append(recording.sent)
        return True

    monkeypatch.setattr(config_apply, "execute", fake_execute)
    async with client(recording) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 60"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        recording.sent = False
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303, reply.text[:300]
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert observed == [False], f"the executor ran {observed!r} after the response was sent"
    assert "The scheduler plugin was reloaded, so the change is live." in landed
    assert apply_bot.reloads == [], "the executor's real body must not have run"


@pytest.mark.asyncio
async def test_a_restart_is_scheduled_in_the_background_too(config_dir, monkeypatch, clean_registry,
                                                           apply_bot):
    seen: list[tuple] = []
    app = app_for(config_dir, monkeypatch)
    recording = RecordingApp(app)

    async def fake_execute(target, ctx):
        seen.append((recording.sent, target.kind, target.node))

    monkeypatch.setattr(config_apply, "execute", fake_execute)
    async with client(recording) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                          MAIN_YAML.replace("My Server", "Background Name"))
        page = await open_page(http, "/settings/main/main")
        confirmation = await open_confirmation(http, page)
        recording.sent = False
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/main/main")
    assert seen == [(True, config_apply.TARGET_RESTART, "")]
    assert f"Restarting the bot on {MASTER}" in landed
    # a bot that CAN record the trail gets no disclosure — the sentence is for the headless install
    assert "not recorded in the audit log" not in landed


@pytest.mark.asyncio
async def test_a_page_that_cannot_reach_a_bot_discloses_that_the_apply_is_not_audited(
        config_dir, monkeypatch, clean_registry):
    """No usable bot in this process means no audit sink at all, so the page SAYS the trail is missing.

    A ``no_discord`` install is NOT this case — its bot appends the row to the audit table and only
    the channel announcement is missing (``audit_available`` answers ``True`` for it). This double
    stands in for a door that can reach no bot: it offers neither a reload nor an audit.
    """
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    class UnreachableBot:
        """A bot the apply door cannot use: no reload half, so ``_bot()`` answers None."""

        __module__ = "services.bot.dummy.bot"

        extensions: dict = {}

        async def audit(self, message, **kwargs):
            return None

    bot = UnreachableBot()
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        app = app_for(config_dir, monkeypatch)
        async with client(app) as http:
            await sign_in(http, ADMIN)
            await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                              MAIN_YAML.replace("My Server", "Headless Name"))
            page = await open_page(http, "/settings/main/main")
            confirmation = await open_confirmation(http, page)
            reply = await post_apply(http, confirm_fields(confirmation))
            assert reply.status_code == 303
            landed = await open_page(http, "/settings/main/main")
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved
    assert "This action is not recorded in the audit log on this install." in landed


# ============================================== 11. the pre-flight refusals

@pytest.mark.asyncio
async def test_a_plugin_that_is_not_loaded_is_refused(config_dir, monkeypatch, clean_registry,
                                                      apply_bot):
    apply_bot.extensions.clear()
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 70"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert "the scheduler plugin is not loaded in this process" in landed
    assert apply_bot.reloads == []
    assert (config_dir / "plugins" / "scheduler.yaml").read_text(encoding="utf-8").count(
        "startup_delay: 70") == 1


@pytest.mark.asyncio
async def test_an_unknown_node_is_refused(config_dir, monkeypatch, clean_registry, apply_bot):
    app = app_for(config_dir, monkeypatch, master="no-such-node")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                          MAIN_YAML.replace("My Server", "Another Name"))
        page = await open_page(http, "/settings/main/main")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/main/main")
    assert "knows no node named no-such-node" in landed
    assert config_apply.in_flight("no-such-node") is False


@pytest.mark.asyncio
async def test_a_restart_already_in_flight_is_refused(config_dir, monkeypatch, clean_registry,
                                                      apply_bot):
    config_apply.mark_in_flight(MASTER)
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/main/main", "/settings/main/main",
                          MAIN_YAML.replace("My Server", "Yet Another Name"))
        page = await open_page(http, "/settings/main/main")
        confirmation = await open_confirmation(http, page)
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/main/main")
    assert f"a restart of {MASTER} is already in flight" in landed


@pytest.mark.asyncio
async def test_an_offer_the_file_has_outrun_is_refused(config_dir, monkeypatch, clean_registry,
                                                       apply_bot):
    """The file changed again after the save: the offer is refused instead of applied blind."""
    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 80"))
        page = await open_page(http, "/settings/plugin/scheduler")
        confirmation = await open_confirmation(http, page)
        (config_dir / "plugins" / "scheduler.yaml").write_text(
            SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 90"), encoding="utf-8")
        reply = await post_apply(http, confirm_fields(confirmation))
        assert reply.status_code == 303
        landed = await open_page(http, "/settings/plugin/scheduler")
    assert "no longer valid" in landed
    assert apply_bot.reloads == []


# ============================================== 12. the executor itself

@pytest.mark.asyncio
async def test_the_executor_reloads_the_named_plugin_and_audits_it(apply_bot):
    from core.actions import ActionContext

    target = config_apply.ApplyTarget(config_apply.TARGET_PLUGIN, plugin="scheduler")
    ctx = ActionContext(node=None, bus=None)
    ctx._audit_user = "tester"
    await config_apply.execute(target, ctx)
    assert apply_bot.reloads == ["scheduler"]
    assert apply_bot.calls and "reloaded the scheduler plugin" in apply_bot.calls[0][0]


# ============================================== 13. the door speaks the reader's language

@pytest.mark.asyncio
async def test_the_confirmation_renders_in_the_readers_language(config_dir, monkeypatch,
                                                               clean_registry, apply_bot):
    """The confirmation's copy is chrome: translated, with the plugin NAME left verbatim."""
    from services.webservice import i18n

    app = app_for(config_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        await save_expert(http, "/actions/settings/config/plugin/scheduler",
                          "/settings/plugin/scheduler",
                          SCHEDULER_YAML.replace("startup_delay: 10", "startup_delay: 20"))
        page = await open_page(http, "/settings/plugin/scheduler")
        control = apply_control(page)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        reply = await http.post(control["action"], data=control["fields"])
    assert reply.status_code == 200
    body = reply.text
    assert "Das Plugin scheduler jetzt neu laden?" in body
    assert "Reload the scheduler plugin now?" not in body
    assert "Ziel:" in body


@pytest.mark.asyncio
async def test_the_executor_restarts_the_node_through_the_node_operation(clean_registry, apply_bot):
    """A restart is not a second implementation: it is the node operation the console offers."""
    from core.actions import ActionContext, discover_actions

    discover_actions(["admin"])
    assert "restart_node" in discover_actions([])
    started: list[str] = []
    node = types.SimpleNamespace(name=NODE_A)

    async def restart():
        started.append(NODE_A)

    node.restart = restart
    ctx = ActionContext(node=None, bus=types.SimpleNamespace(nodes={NODE_A: node}))
    ctx._audit_user = "tester"
    await config_apply.execute(config_apply.ApplyTarget(config_apply.TARGET_RESTART, node=NODE_A),
                               ctx)
    assert started == [NODE_A]


# ============================== the unreachable-bot refusal names WHICH piece is missing
# "the bot process cannot be reached from here" sent an operator whose bot was running looking in
# the wrong place: the ONE sentence covered four different installs. These pin the four reasons and
# the sentence the page shows for one of them.

@pytest.fixture
def bot_slot():
    """The BotService slot, writable per test, restored afterwards."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    saved = ServiceRegistry._singletons.get(BotService)
    had = BotService in ServiceRegistry._singletons
    try:
        yield lambda value: ServiceRegistry._singletons.__setitem__(BotService, value)
    finally:
        if had:
            ServiceRegistry._singletons[BotService] = saved
        else:
            ServiceRegistry._singletons.pop(BotService, None)


def test_a_missing_bot_service_is_named(bot_slot):
    bot_slot(None)
    bot, why = config_apply._bot_diagnosis()
    assert bot is None and why == config_apply.WHY_NO_SERVICE


def test_a_service_without_a_bot_is_named(bot_slot):
    bot_slot(types.SimpleNamespace(bot=None))
    bot, why = config_apply._bot_diagnosis()
    assert bot is None and why == config_apply.WHY_NO_BOT


def test_a_bot_with_no_reload_is_named(bot_slot):
    bot_slot(types.SimpleNamespace(bot=types.SimpleNamespace()))
    bot, why = config_apply._bot_diagnosis()
    assert bot is None and why == config_apply.WHY_NO_RELOAD


def test_a_headless_bot_is_named_as_such(bot_slot):
    """A ``no_discord`` install gets its own sentence — it is a config choice, not a fault."""
    class Headless:
        __module__ = config_apply.HEADLESS_BOT_MODULE

    bot_slot(types.SimpleNamespace(bot=Headless()))
    bot, why = config_apply._bot_diagnosis()
    assert bot is None and why == config_apply.WHY_HEADLESS


def test_a_reloadable_bot_passes(bot_slot):
    class Real:
        async def reload_plugin(self, plugin):
            return True

    real = Real()
    bot_slot(types.SimpleNamespace(bot=real))
    assert config_apply._bot_diagnosis() == (real, "")


def test_a_bot_with_only_the_two_halves_still_passes(bot_slot):
    """``reload_plugin`` is a convenience; a bot that offers unload+load is just as usable."""
    class Halves:
        async def unload_plugin(self, plugin):
            return True

        async def load_plugin(self, plugin):
            return True

    halves = Halves()
    bot_slot(types.SimpleNamespace(bot=halves))
    assert config_apply._bot_diagnosis() == (halves, "")


def test_a_plugin_carried_as_a_cog_is_found_when_there_is_no_extension_map(bot_slot):
    """The page proves the plugin is loaded; a douple that only lists COGS must not be told it is not."""
    class Bare:
        cogs = {"Scheduler": object()}

        async def reload_plugin(self, plugin):
            return True

    bot_slot(types.SimpleNamespace(bot=Bare()))
    loaded = config_apply._loaded_extensions(Bare())
    assert "plugins.scheduler.commands" in loaded


def test_a_plugin_carried_as_a_cog_is_found_beside_an_extension_map_of_other_plugins(bot_slot):
    """The extension map holding OTHER plugins must not answer "not loaded" about a running cog.

    A real bot loads some plugins as extensions and every plugin as a cog; the door asks one
    question — is this plugin here — so both answers are read.
    """
    class Mixed:
        extensions = {"plugins.admin.commands": object()}
        cogs = {"Scheduler": object()}

    loaded = config_apply._loaded_extensions(Mixed())
    assert "plugins.admin.commands" in loaded, "the extension map is still read"
    assert "plugins.scheduler.commands" in loaded, "and so is the cog that is really loaded"
