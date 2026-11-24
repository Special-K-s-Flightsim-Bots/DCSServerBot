"""The Scheduler wizard's ONE write guard: who may write which block, and what is refused.

The guard is the only place a posted ``_scope`` becomes a block of ``config/plugins/scheduler.yaml``,
so every refusal is pinned here against the FILE, not against a return value: a refused save leaves
the document byte-identical. Driven through the real application with the real local login backend and
a stub cluster, so the capability check, the server resolver and the session CSRF are the production
ones.

The four refusals the guard owns — an empty scope, a reserved section, a name that is not a server of
this installation, and a node — plus the plan-key refusals that sit beside it in
``scheduler_plan.posted_key_refusal`` (``startup_delay``, the deprecated ``local_times``/``utc_times``
and any name outside the tab's own key set).
"""
from __future__ import annotations

from pathlib import Path

import hashlib
import re
import types

import pytest

from conftest import permissions
from services.webservice import scheduler_plan
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, server, sign_in,
)

ADMIN = "specialk"
MANAGER = "mod1"
SERVER_A = "SRS-1"
INSTANCE_A = "Host-A-Instance"
INSTANCE_B = "Host-B-Instance"
#: a SERVER name that is also a node name — the only shape that reaches the node branch.
SERVER_NAMED_AS_NODE = "dcs-a-01"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    {instance_a}:
      home: /tmp/a
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    {instance_b}:
      home: /tmp/b
""".format(instance_a=INSTANCE_A, instance_b=INSTANCE_B)

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
SRS-B:
  node: dcs-b-01
"""

#: a server's own block, a second server's block, the shared block and a comment on each — the file a
#: refusal must leave untouched.
SCHEDULER_YAML = """\
# the install's scheduler configuration
DEFAULT:
  startup_delay: 10                                # shared by every server
  warn:
    message: '!!! {item} will {what} in {when} !!!'
    times:
    - 600
    - 60
@A@:
  timezone: Europe/Berlin
  schedule:
    00-12: YYYYYYY
    12-24: NNNNNNN
@B@:
  timezone: UTC
""".replace("@A@", INSTANCE_A).replace("@B@", INSTANCE_B)


@pytest.fixture
def scope_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "scheduler.yaml").write_text(SCHEDULER_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(scope_dir, monkeypatch):
    """Bind the bot's own validator to THIS install's cluster, as production binds it at startup."""
    import core.commandline as commandline
    from core.utils import validators

    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(scope_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance


def cluster():
    """Server A on Host A, server B on Host B — the page's own two sides, plus both instances."""
    a = server(SERVER_A, ["Host A"])
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance=INSTANCE_B)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False, instances={})
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                   instances=[instance(INSTANCE_A, a), instance(INSTANCE_B, b)])


def node_named_cluster():
    """The same fleet plus a server whose NAME is a node's name.

    A pure node name never reaches the guard's node branch: ``server_named`` misses it first and the
    answer is the generic unknown-name sentence. Only a name that resolves BOTH ways distinguishes
    the two refusals, which is why this shape exists.
    """
    a = server(SERVER_A, ["Host A"])
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance=INSTANCE_B)
    clash = server(SERVER_NAMED_AS_NODE, ["Host A"], node_name=SERVER_NAMED_AS_NODE,
                   instance="Clash-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False, instances={})
    return Cluster([a, b, clash], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                   instances=[instance(INSTANCE_A, a), instance(INSTANCE_B, b)])


def app_for(scope_dir):
    return build(scope_dir, cluster(), plugins=("scheduler",))


def scheduler_path(scope_dir):
    return scope_dir / "plugins" / "scheduler.yaml"


def digest(scope_dir) -> str:
    return hashlib.sha256(scheduler_path(scope_dir).read_bytes()).hexdigest()


def csrf_of(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


async def token(http, path: str = "/fleet/SRS-1") -> str:
    """A session CSRF token, read out of a RENDERED page (never minted by the test)."""
    page = await http.get(path)
    assert page.status_code == 200, page.text[:200]
    return csrf_of(page.text)


async def post_save(http, data: dict):
    return await http.post(settings_page.SCHEDULER_SAVE_PATH, data=data)


# =========================================================== 1. the capability and the CSRF gate

@pytest.mark.asyncio
async def test_a_manager_writes_their_own_server_and_cannot_reach_the_shared_default(
        scope_dir, node_data, clean_registry):
    """A manager (a DCS role whose scope holds the server) owns that server's schedule exactly.

    The per-server write is theirs; ``DEFAULT`` is not, because it moves every server including the
    ones they do not own — so the flag is refused as a NOTICE that leaves the file byte-identical,
    never as a write.
    """
    app = app_for(scope_dir)
    async with client(app) as http:
        await sign_in(http, "hoster")
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "timezone": "UTC"})
        assert reply.status_code == 303, reply.text[:300]
        assert "timezone: UTC" in scheduler_path(scope_dir).read_text(encoding="utf-8"), \
            "the manager's own server was written"

        before = digest(scope_dir)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "_shared_default": "1",
                                       "timezone": "Europe/Paris"})
    assert reply.status_code == 303, "a refusal is a notice and a redirect, never a raw 403"
    assert digest(scope_dir) == before, "the shared DEFAULT was not touched"


@pytest.mark.asyncio
async def test_a_dcs_admin_writes_the_server_its_scope_holds(scope_dir, node_data, clean_registry):
    """The DCS Admin's own server is writable: the guard resolves it and the block changes."""
    app = app_for(scope_dir)
    async with client(app) as http:
        await sign_in(http, MANAGER)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "timezone": "UTC"})
    assert reply.status_code == 303, reply.text[:300]
    assert "UTC" in scheduler_path(scope_dir).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_dcs_admin_cannot_name_a_server_outside_its_scope(scope_dir, node_data,
                                                                 clean_registry):
    """A server tagged to a role the caller does not hold: refused, nothing written, no disclosure."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, MANAGER)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": "SRS-B",
                                       "timezone": "Europe/Berlin"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_a_dcs_admin_cannot_post_the_expert_field(scope_dir, node_data, clean_registry):
    """The expert door is checked AT THE ROUTE: the flag plus block text writes nothing."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    block = "timezone: America/New_York\n"
    async with client(app) as http:
        await sign_in(http, MANAGER)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       settings_page.SETTINGS_EXPERT_FIELD: "1",
                                       scheduler_plan.BLOCK_YAML_FIELD: block})
        verdict = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                  data={"_csrf_token": token_text, "_scope": SERVER_A,
                                        scheduler_plan.BLOCK_YAML_FIELD: block})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before
    body = verdict.json()
    assert body["ok"] is False and body["kind"] == "scope"
    assert "Expert Mode" in body["message"]


@pytest.mark.asyncio
async def test_a_post_without_the_csrf_token_is_refused(scope_dir, node_data, clean_registry):
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await post_save(http, {"_scope": SERVER_A, "timezone": "UTC"})
    assert reply.status_code == 403, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_an_installation_without_a_scheduler_file_answers_404(scope_dir, node_data,
                                                                    clean_registry):
    scheduler_path(scope_dir).unlink()
    app = app_for(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "timezone": "UTC"})
    assert reply.status_code == 404, reply.text[:300]


# ================================================================ 2. the scope refusals, by name

@pytest.mark.asyncio
@pytest.mark.parametrize("posted_scope", ["", "   "])
async def test_an_empty_scope_is_refused(scope_dir, node_data, clean_registry, posted_scope):
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": posted_scope,
                                       "timezone": "UTC"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("posted_scope", ["DEFAULT", "commands", "chat_commands"])
async def test_each_reserved_section_is_refused_by_its_exact_name(scope_dir, node_data,
                                                                  clean_registry, posted_scope):
    """``DEFAULT`` and the two command sections are not server blocks and are never writable as one."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": posted_scope,
                                       "startup_delay": "99"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before
    assert "startup_delay: 10" in scheduler_path(scope_dir).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_name_that_is_no_server_of_this_installation_is_refused(scope_dir, node_data,
                                                                        clean_registry):
    """A block the file carries for a server this installation does not have is not addressable."""
    app = app_for(scope_dir)
    path = scheduler_path(scope_dir)
    path.write_text(path.read_text(encoding="utf-8") + "Ghost-Instance:\n  timezone: UTC\n",
                    encoding="utf-8")
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": "Ghost-Instance",
                                       "timezone": "Europe/Berlin"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_a_node_name_that_is_no_server_is_refused(scope_dir, node_data, clean_registry):
    """A bare node name is refused at the SERVER lookup — the node branch is not reached."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": "dcs-a-01",
                                       "timezone": "UTC"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_a_name_that_is_both_a_server_and_a_node_is_refused_as_a_node(
        scope_dir, node_data, clean_registry):
    """The node branch's own sentence, on a name that resolves as a server AND as a node."""
    app = build(scope_dir, node_named_cluster(), plugins=("scheduler",))
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_NAMED_AS_NODE,
                                       "timezone": "UTC"})
        assert reply.status_code == 303, reply.text[:300]
        assert reply.headers["location"] == settings_page.SCHEDULER_FALLBACK_URL
        landing = await http.get("/settings")
    assert digest(scope_dir) == before
    assert landing.status_code == 200
    assert settings_page.SCHEDULER_SCOPE_NODE in landing.text
    assert settings_page.SCHEDULER_SCOPE_UNKNOWN not in landing.text


@pytest.mark.asyncio
async def test_a_refusal_is_one_sentence_on_the_page_it_returns_to(scope_dir, node_data,
                                                                   clean_registry):
    """The refusal is a one-shot NOTICE — never a page that leaves the operator guessing."""
    app = app_for(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": "DEFAULT",
                                       "timezone": "UTC"})
        assert reply.status_code == 303
        # the one-shot notice is popped by the NEXT page this session renders, whichever it is
        landing = await http.get("/settings")
    assert landing.status_code == 200
    assert "not server blocks" in landing.text


# ============================================================ 3. the plan-key refusals beside it

@pytest.mark.asyncio
async def test_startup_delay_is_refused(scope_dir, node_data, clean_registry):
    """A DEFAULT-level value shared by every server — the tab refuses it by its own sentence."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "startup_delay": "99"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["action.0.local_times", "action.0.utc_times"])
async def test_the_deprecated_action_keys_are_refused_where_they_occur(scope_dir, node_data,
                                                                       clean_registry, field):
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       field: "04:00", "action.0.method": "restart"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["Host-B-Instance.timezone", "DEFAULT.warn.message",
                                   "dcs-a-01.Host-A-Instance.timezone", "elsewhere"])
async def test_a_plan_naming_another_block_is_refused(scope_dir, node_data, clean_registry, field):
    """A crafted field name cannot reach another block: the tab's own key set is the allow-list."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       field: "Europe/Berlin"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


# ============================================================== 4. the allow-list is the plan's own

def test_the_tabs_key_set_is_the_element_schema_minus_startup_delay():
    assert scheduler_plan.WIZARD_KEYS == (
        "timezone", "schedule", "warn", "action", "startup",
        "onSimulationStart", "onSimulationStop", "onMissionEnd", "onShutdown")
    assert "startup_delay" not in scheduler_plan.WIZARD_KEYS


def test_the_reserved_sections_are_the_files_three_non_server_keys():
    assert settings_page.SCHEDULER_RESERVED_SECTIONS == {"DEFAULT", "commands", "chat_commands"}


def test_both_scheduler_posts_are_declared_against_the_scheduler_write():
    """Both POSTs are declared, and both demand ``scheduler.write`` (not the Settings one).

    A missing declaration fails CLOSED at the deny-by-default gate, so the wizard's two writes would
    answer 403 while every other test stayed green. Declaring them against ``settings.write`` would
    keep DCS Admin out of a door its own role now opens.
    """
    declared = settings_page.capabilities()
    assert declared[settings_page.SCHEDULER_SAVE_PATH] == "scheduler.write"
    assert declared[settings_page.SCHEDULER_VALIDATE_PATH] == "scheduler.write"
    assert settings_page.SCHEDULER_WRITE_CAPABILITY == "scheduler.write"
    assert settings_page.SETTINGS_WRITE_CAPABILITY == "settings.write"


def test_the_settings_sections_stay_admin_only():
    """DEFAULT, commands and chat_commands stay in the Settings area — ``Admin`` only, unscoped."""
    settings_page.capabilities()
    for capability in (settings_page.SETTINGS_VIEW_CAPABILITY,
                       settings_page.SETTINGS_WRITE_CAPABILITY,
                       settings_page.SETTINGS_FILE_VIEW_CAPABILITY,
                       settings_page.SETTINGS_FILE_WRITE_CAPABILITY):
        assert permissions.roles_for_capability(capability) == ("Admin",), capability
        assert permissions.scope_grants(capability) is False, capability


def test_access_is_narrowed_by_the_server_scope(monkeypatch):
    """Role x scope, in the one function: a server tagged to another role earns nothing.

    The predicate is ``scope.server_in_scope`` — the same rule the scoped source applies — so a
    capability held by role cannot be spent on a server the caller's ``managed_by`` excludes.
    """
    from services.webservice.scope import Scope

    permissions.set_role_resolver(lambda request: {"DCS Admin"})
    permissions.set_scope_resolver(lambda request: Scope.restricted(["Host A"]))
    permissions.set_manager_resolver(lambda request: False)
    try:
        inside = server("SRS-1", ["Host A"])
        outside = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance=INSTANCE_B)
        granted = settings_page.scheduler_access(object(), inside)
        refused = settings_page.scheduler_access(object(), outside)
    finally:
        permissions.set_role_resolver(None)
        permissions.set_scope_resolver(None)
        permissions.set_manager_resolver(None)
    assert (granted.view, granted.write) == (True, True)
    assert (refused.view, refused.write, refused.expert) == (False, False, False)


def test_every_door_reads_one_access_function():
    """The strip, the tab refusal, the landing and both POSTs call ``scheduler_access``.

    Asserted on the SOURCE: a door that re-spelled ``permissions.allows`` would agree today and drift
    tomorrow, which is exactly the tab-offers-what-the-POST-refuses defect.
    """
    page_dir = Path(__file__).resolve().parent.parent / "services" / "webservice" / "pages"
    detail = (page_dir / "server_detail.py").read_text(encoding="utf-8")
    settings = (page_dir / "settings.py").read_text(encoding="utf-8")
    assert "permissions.allows(SCHEDULER_CAPABILITY" not in detail
    assert detail.count("permissions.allows(SCHEDULER_VIEW_CAPABILITY") == 0
    # the three capability reads live in ONE function (scheduler_access), never at a door
    assert settings.count("permissions.allows(SCHEDULER_VIEW_CAPABILITY") == 1
    assert settings.count("permissions.allows(SCHEDULER_WRITE_CAPABILITY") == 1
    assert settings.count("permissions.allows(SCHEDULER_EXPERT_CAPABILITY") == 1  # expert = shared
    # the strip, the wizard refusal and the tab context
    assert detail.count("scheduler_access(request") >= 3
    # the guard, the two expert checks and the two shared-default checks, always on a RESOLVED
    # server (never on a posted name)
    assert settings.count("scheduler_access(request, ") == 5
    assert "require_file_capability(request, SCHEDULER_KIND" not in settings


def test_the_scope_field_is_its_own_name_never_a_plan_key():
    assert settings_page.SCHEDULER_SCOPE_FIELD == "_scope"
    assert settings_page.SCHEDULER_SCOPE_FIELD not in scheduler_plan.WIZARD_KEYS


@pytest.mark.asyncio
async def test_a_dcs_admin_cannot_reach_the_shared_sections(scope_dir, node_data, clean_registry):
    """DEFAULT, commands and chat_commands are Settings' own: refused on BOTH doors."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, MANAGER)
        token_text = await token(http)
        reserved = await post_save(http, {"_csrf_token": token_text, "_scope": "DEFAULT",
                                          "timezone": "UTC"})
        commands = await post_save(http, {"_csrf_token": token_text, "_scope": "commands",
                                          "timezone": "UTC"})
        file_door = await http.get(settings_page.SETTINGS_FILE_PATH.format(
            kind="plugin", name="scheduler"))
    assert reserved.status_code == 303 and commands.status_code == 303
    assert digest(scope_dir) == before
    assert file_door.status_code == 403
