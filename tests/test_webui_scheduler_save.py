"""The Scheduler wizard's save and its validate route: the minimal override, and nothing else moves.

The write goes through ``configwrite.patch_config_file`` — one per-file lock, a round trip that keeps
comments and key order, schema validation of the candidate, an atomic write and a ``.bak`` — so these
pins are about the OVERRIDE it is handed and about the file afterwards: a value equal to the
inherited one is not written, a key the plan stops stating is deleted, another server's block and
DEFAULT come out byte-identical, a stale form and an invalid plan write nothing, and a server that
inherited gets its block created inside the same write.

The cluster is bound the way production binds it (``commandline.COMMAND_LINE_ARGS`` plus a reset
``validators.NodeData``), because the scheduler schema's root ``check_main_structure`` reads
``nodes.yaml`` through that singleton and raises when it cannot.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import types
from urllib.parse import urlencode

import pytest

from services.webservice import scheduler_plan
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, instance, server, sign_in,
)

ADMIN = "specialk"
SERVER_A = "SRS-1"
SERVER_B = "SRS-B"
INSTANCE_A = "Host-A-Instance"
INSTANCE_B = "Host-B-Instance"

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
    @A@:
      home: /tmp/a
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    @B@:
      home: /tmp/b
""".replace("@A@", INSTANCE_A).replace("@B@", INSTANCE_B)

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
SRS-B:
  node: dcs-b-01
"""

#: DEFAULT with a shared warning ladder, one server's block and another server's block — the three
#: things a save must never move. The comments are part of the assertion.
SCHEDULER_YAML = """\
# the install's scheduler configuration
DEFAULT:
  startup_delay: 10                                # shared by every server
  timezone: UTC
  warn:
    message: 'restarting soon'                     # the shared text
    times:
    - 600
    - 60
@A@:
  timezone: Europe/Berlin                          # this server's own zone
  schedule:
    00-12: YYYYYYY
@B@:
  timezone: America/New_York
""".replace("@A@", INSTANCE_A).replace("@B@", INSTANCE_B)

INSTANCE_A_BLOCK = {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"}}
INSTANCE_B_BLOCK = {"timezone": "America/New_York"}
DEFAULT_BLOCK = {"startup_delay": 10, "timezone": "UTC",
                 "warn": {"message": "restarting soon", "times": [600, 60]}}


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
    a = server(SERVER_A, ["Host A"])
    b = server(SERVER_B, ["Host B"], node_name="dcs-b-01", instance=INSTANCE_B)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False, instances={})
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                   instances=[instance(INSTANCE_A, a), instance(INSTANCE_B, b)])


class FakeScheduler:
    """The plugin double: ``get_config`` is the ONE call the tab makes on it."""

    def __init__(self, plan):
        self.plan = plan

    def get_config(self, server_obj=None, **_kwargs):
        return self.plan


def effective():
    """What the plugin's own merge answers for server A: DEFAULT plus its own block."""
    plan = {key: value for key, value in DEFAULT_BLOCK.items()}
    plan.update({"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"}})
    return plan


def app_for(scope_dir, monkeypatch, plan=None):
    """The real app with the scheduler PLUGIN doubled — through monkeypatch, so it is undone."""
    app = build(scope_dir, cluster(), plugins=("scheduler",))
    monkeypatch.setattr(detail_page, "_scheduler",
                        lambda: FakeScheduler(plan if plan is not None else effective()))
    return app


def scheduler_path(scope_dir):
    return scope_dir / "plugins" / "scheduler.yaml"


def text_of(scope_dir) -> str:
    return scheduler_path(scope_dir).read_text(encoding="utf-8")


def digest(scope_dir) -> str:
    return hashlib.sha256(scheduler_path(scope_dir).read_bytes()).hexdigest()


def read_block(scope_dir, name):
    from core.utils import configwrite

    document = configwrite.load_config_file(scheduler_path(scope_dir))
    return dict(document.get(name) or {})


def plan_fields(plan) -> dict:
    """The form a step posts for *plan*: its visible values plus the shape markers."""
    data = {field["name"]: field["value"]
            for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)}
    return data


async def csrf_of(http) -> str:
    page = await http.get("/settings")
    assert page.status_code == 200, page.text[:200]
    return re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)


async def revision_of(http) -> str:
    page = await http.get("/settings/plugin/scheduler")
    assert page.status_code == 200, page.text[:200]
    return re.search(r'name="_revision" value="([^"]*)"', page.text).group(1)


async def save(http, data: dict):
    return await http.post(settings_page.SCHEDULER_SAVE_PATH, data=data)


# ======================================================= 1. the minimal override, key by key

@pytest.mark.asyncio
async def test_a_changed_value_is_written_and_the_rest_of_the_file_is_identical(
        scope_dir, node_data, clean_registry, monkeypatch):
    """One key changes; DEFAULT, the other server's block and every comment stay as they were."""
    app = app_for(scope_dir, monkeypatch)
    before = text_of(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A,
                                  "timezone": "Europe/Berlin", "schedule.00-12": "NNNNNNN"})
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == {"timezone": "Europe/Berlin",
                                                 "schedule": {"00-12": "NNNNNNN"}}
    assert read_block(scope_dir, INSTANCE_B) == INSTANCE_B_BLOCK
    assert read_block(scope_dir, "DEFAULT") == DEFAULT_BLOCK
    after = text_of(scope_dir)
    for comment in ("# the install's scheduler configuration", "# shared by every server",
                    "# this server's own zone"):
        assert comment in after, comment
    # the other blocks' own lines are byte-identical
    assert before.split("@A@")[0] in before
    assert after.count("America/New_York") == 1


@pytest.mark.asyncio
async def test_a_value_equal_to_the_inherited_one_is_not_written(scope_dir, node_data,
                                                                 clean_registry, monkeypatch):
    """The plan states DEFAULT's own zone: the server's block gains nothing and loses nothing."""
    app = app_for(scope_dir, monkeypatch)
    plan = effective() | {"timezone": "UTC"}
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A} | plan_fields(plan))
    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert "timezone" not in block, "a value equal to the inherited one was written anyway"
    assert block["schedule"] == {"00-12": "YYYYYYY"}


@pytest.mark.asyncio
async def test_a_key_the_plan_stops_stating_is_deleted_so_inheritance_is_expressible(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The server has its own schedule; the plan no longer states one — the key is DELETED."""
    app = app_for(scope_dir, monkeypatch)
    plan = {"timezone": "Europe/Berlin"}
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A} | plan_fields(plan))
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == {"timezone": "Europe/Berlin"}


@pytest.mark.asyncio
async def test_an_untouched_save_writes_nothing(scope_dir, node_data, clean_registry, monkeypatch):
    """The plan is exactly the effective plan: every value is inherited or already written."""
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    plan = {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"}}
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A} | plan_fields(plan))
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_a_server_that_inherited_gets_its_block_created_with_only_the_overrides(
        scope_dir, node_data, clean_registry, monkeypatch):
    """A server with no block of its own: the save CREATES one, carrying only the changed key."""
    app = build(scope_dir, cluster(), plugins=("scheduler",))
    monkeypatch.setattr(detail_page, "_scheduler",
                        lambda: FakeScheduler({"timezone": "UTC"}))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        # server B's block is REMOVED first, so it inherits; then a save states its own zone
        path = scheduler_path(scope_dir)
        path.write_text(text_of(scope_dir).split(f"{INSTANCE_B}:")[0], encoding="utf-8")
        reply = await save(http, {"_csrf_token": token_text, "_revision": "",
                                  "_scope": SERVER_B, "timezone": "America/New_York"})
    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_B)
    assert block == {"timezone": "America/New_York"}, "DEFAULT must never be copied down"
    assert read_block(scope_dir, "DEFAULT") == DEFAULT_BLOCK


@pytest.mark.asyncio
async def test_the_backup_is_written_beside_the_file(scope_dir, node_data, clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        await save(http, {"_csrf_token": token_text, "_revision": revision, "_scope": SERVER_A,
                          "timezone": "Europe/Berlin", "schedule.00-12": "NNNNNNN"})
    assert (scope_dir / "plugins" / "scheduler.yaml.bak").is_file()


@pytest.mark.asyncio
async def test_an_existing_deprecated_key_survives_a_save(scope_dir, node_data, clean_registry, monkeypatch):
    """``local_times`` is refused as a POSTED value but never removed from the file by a save."""
    path = scheduler_path(scope_dir)
    path.write_text(text_of(scope_dir).replace(
        "  schedule:\n    00-12: YYYYYYY",
        "  schedule:\n    00-12: YYYYYYY\n  action:\n    local_times:\n    - 04:00\n    method: restart"),
        encoding="utf-8")
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A, "timezone": "Europe/Berlin",
                                  "schedule.00-12": "NNNNNNN",
                                  "action.0.method": "restart", "shape.action": "map"})
    assert reply.status_code == 303, reply.text[:300]
    assert "local_times" in text_of(scope_dir)
    assert "- 04:00" in text_of(scope_dir)


#: one action entry carrying ``populated: false`` and the deprecated ``local_times`` — the two keys a
#: save must treat differently when the plan stops stating one of them.
ACTION_BLOCK = ("  action:\n"
                "    method: restart                                 # the entry's own method\n"
                "    populated: false\n"
                "    local_times:\n"
                "    - 04:00\n")


def with_action(scope_dir) -> None:
    """The scheduler file with one ``action`` entry on server A's block, comments included."""
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace("  schedule:\n    00-12: YYYYYYY",
                                   "  schedule:\n    00-12: YYYYYYY\n" + ACTION_BLOCK),
        encoding="utf-8")


@pytest.mark.asyncio
async def test_an_entry_switched_to_act_with_players_on_loses_its_populated_key(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The radio posts no key, so the save REMOVES the ``populated: false`` the file carries.

    A write merges the maps it is handed, so without the scoped sub-key prune the entry would keep
    waiting for an empty server while the page says it acts with players on. What the prune may not
    touch is the rest of the entry: the deprecated ``local_times`` — outside the key set a prune is
    allowed to consider — stays exactly where it is.
    """
    with_action(scope_dir)
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A,
                                  "action.0.method": "restart", "shape.action": "map"})
    assert reply.status_code == 303, reply.text[:300]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert "populated" not in entry, "the operator's choice must not be silently ignored"
    assert entry["local_times"] == ["04:00"], "a deprecated key the plan never states survives"
    assert entry["method"] == "restart"
    assert "# the entry's own method" in text_of(scope_dir), "the comment survives the prune"


@pytest.mark.asyncio
async def test_a_startup_key_the_plan_stops_stating_is_removed(scope_dir, node_data,
                                                               clean_registry, monkeypatch):
    """The startup block is pruned the same way: a cleared control removes its key, not the block.

    ``password`` is the exception and stays: the step draws no control for it, so it rides as a
    carried value and is never pruned or rewritten by this step.
    """
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace(
            "  schedule:\n    00-12: YYYYYYY",
            "  schedule:\n    00-12: YYYYYYY\n  startup:\n    mission_id: 3\n"
            "    clear_maintenance: true\n    password: hunter2"),
        encoding="utf-8")
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A, "startup.mission_id": "3"})
    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert block["startup"]["mission_id"] == 3
    assert "clear_maintenance" not in block["startup"], "the cleared key must be gone, the rest kept"
    assert block["startup"]["password"] == "hunter2", \
        "a key this step never draws survives the save untouched"


# ------------------------------------------- step 2: the startup switch and the one mission pick

#: server A's block with a startup that states BOTH mission keys — the shape the exclusivity removes.
STARTUP_BOTH = ("  schedule:\n"
                "    00-12: YYYYYYY\n"
                "  startup:\n"
                "    mission_id: 3\n"
                "    mission_file: Missions/night.miz\n"
                "    password: hunter2\n")


def with_startup(scope_dir) -> None:
    """The scheduler file with a startup block on server A's block, both mission keys included."""
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace("  schedule:\n    00-12: YYYYYYY", STARTUP_BOTH),
        encoding="utf-8")


_INPUT = re.compile(r"<input\b([^>]*)>", re.S)
_SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_OPTION = re.compile(r'<option value="([^"]*)"([^>]*)>', re.S)


def form_pairs(body: str) -> list[tuple[str, str]]:
    """What a browser submits for *body*'s ONE form: enabled controls only, one pair per option.

    The rule that matters here is the browser's own: a ``disabled`` control posts NOTHING, and an
    unticked checkbox or radio is not posted at all. A multiple select posts one pair per selected
    option, in the page's order.
    """
    pairs: list[tuple[str, str]] = []
    for raw in _INPUT.findall(body):
        attrs = dict(_ATTR.findall(raw))
        name = attrs.get("name")
        if not name or re.search(r"\bdisabled\b", raw) or attrs.get("type") == "submit":
            continue
        if attrs.get("type") in ("checkbox", "radio") and not re.search(r"\bchecked\b", raw):
            continue
        pairs.append((name, attrs.get("value", "")))
    for raw, inner in _SELECT.findall(body):
        attrs = dict(_ATTR.findall(raw))
        name = attrs.get("name")
        if not name or re.search(r"\bdisabled\b", raw):
            continue
        pairs.extend((name, value) for value, rest in _OPTION.findall(inner)
                     if re.search(r"\bselected\b", rest))
    return pairs


async def step_form_pairs(http, extra: str = "", step: str = "2"):
    """The pairs the RENDERED step form submits — the page's own answer, not a hand-built dict."""
    query = f"/fleet/{SERVER_A}?tab=scheduler&step={step}" + (f"&{extra}" if extra else "")
    page = await http.get(query)
    assert page.status_code == 200, page.text[:300]
    return form_pairs(page.text)


async def save_pairs(http, pairs):
    """POST *pairs* as the save form's body, with the console's own carriers first."""
    body = [("_csrf_token", await csrf_of(http)), ("_revision", await revision_of(http)),
            ("_scope", SERVER_A)] + list(pairs)
    return await http.post(settings_page.SCHEDULER_SAVE_PATH, content=urlencode(body),
                           headers={"content-type": "application/x-www-form-urlencoded"})


#: the effective plan with a startup that states BOTH mission keys — what step 2 renders from.
def plan_with_both():
    return effective() | {"startup": {"mission_id": 3, "mission_file": "Missions/night.miz",
                                      "password": "hunter2"}}


@pytest.mark.asyncio
async def test_a_save_with_the_switch_off_states_no_startup_block(scope_dir, node_data,
                                                                 clean_registry, monkeypatch):
    """Off is a DELETION: the keys the step would have written leave the block, the rest stays."""
    with_startup(scope_dir)
    app = app_for(scope_dir, monkeypatch, plan=plan_with_both())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, "startup_on=0")
        reply = await save_pairs(http, pairs)

    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert "startup" not in block, block
    assert block == INSTANCE_A_BLOCK, "everything the step does not own is untouched"
    assert "mission_id" not in text_of(scope_dir), "and no orphan key is left in the file"


@pytest.mark.asyncio
async def test_the_mission_pick_writes_only_the_key_its_radio_names(scope_dir, node_data,
                                                                  clean_registry, monkeypatch):
    """The ID radio submits no file control at all, so the file key the block carried is pruned."""
    with_startup(scope_dir)
    app = app_for(scope_dir, monkeypatch, plan=plan_with_both())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, "startup_pick=mission_id")
        assert not [name for name, _value in pairs if name == "startup.mission_file"], \
            "the pick the radio does not name must not be submitted at all"
        reply = await save_pairs(http, pairs)

    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A)["startup"] == {"mission_id": 3,
                                                           "password": "hunter2"}


@pytest.mark.asyncio
async def test_the_file_pick_writes_only_the_key_its_radio_names(scope_dir, node_data,
                                                                clean_registry, monkeypatch):
    """The reverse: the file control submits, the ID control is disabled and posts nothing."""
    with_startup(scope_dir)
    app = app_for(scope_dir, monkeypatch, plan=plan_with_both())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, "startup_pick=mission_file")
        assert not [name for name, _value in pairs if name == "startup.mission_id"], \
            "the ID control is rendered disabled, so it cannot compete with the file pick"
        reply = await save_pairs(http, pairs)

    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A)["startup"] == {
        "mission_file": "Missions/night.miz", "password": "hunter2"}


# ================================================================ 2. what a refusal writes: nothing

@pytest.mark.asyncio
async def test_a_stale_revision_writes_nothing(scope_dir, node_data, clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": "0" * 32,
                                  "_scope": SERVER_A, "timezone": "America/New_York"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_a_plan_the_schema_refuses_writes_nothing(scope_dir, node_data, clean_registry, monkeypatch):
    """A seven-letter pattern is the schema's own rule; ``YYY`` is refused and the file is untouched."""
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        revision = await revision_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A, "schedule.00-12": "YYY"})
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == before


# ==================================================================== 3. the ambiguous instance name

@pytest.mark.asyncio
async def test_an_ambiguous_instance_name_is_written_nested_under_its_node(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The same instance name on two nodes: the block key is ``(node, instance)``, as the validator demands."""
    scheduler_path(scope_dir).write_text(
        "DEFAULT:\n  timezone: UTC\n", encoding="utf-8")
    (scope_dir / "nodes.yaml").write_text(
        NODES_YAML.replace(f"    {INSTANCE_B}:\n      home: /tmp/b",
                           f"    {INSTANCE_A}:\n      home: /tmp/b"),
        encoding="utf-8")
    (scope_dir / "servers.yaml").write_text(SERVERS_YAML.replace(
        "SRS-B:\n  node: dcs-b-01", f"{SERVER_B}:\n  node: dcs-b-01"), encoding="utf-8")
    a = server(SERVER_A, ["Host A"])
    b = server(SERVER_B, ["Host B"], node_name="dcs-b-01", instance=INSTANCE_A)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False, instances={})
    shared = Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                     instances=[instance(INSTANCE_A, a), instance(INSTANCE_A, b)])
    import core.commandline as commandline
    from core.utils import validators

    validators.NodeData._instance = None
    app = build(scope_dir, shared, plugins=("scheduler",))
    monkeypatch.setattr(detail_page, "_scheduler", lambda: FakeScheduler({"timezone": "UTC"}))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": "",
                                  "_scope": SERVER_A, "timezone": "America/New_York"})
    assert reply.status_code == 303, reply.text[:300]
    document = read_text_document(scope_dir)
    assert "dcs-a-01" in document, "an ambiguous instance name must be nested under its node"
    assert document["dcs-a-01"][INSTANCE_A]["timezone"] == "America/New_York"


def read_text_document(scope_dir):
    from core.utils import configwrite

    return configwrite.load_config_file(scheduler_path(scope_dir))


# ======================================================================== 4. the validate route

@pytest.mark.asyncio
async def test_the_validate_route_blesses_a_valid_plan_and_writes_nothing(scope_dir, node_data,
                                                                         clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reply = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                data={"_csrf_token": token_text, "_scope": SERVER_A,
                                      "timezone": "Europe/Berlin", "schedule.00-12": "NNNNNNN"})
    assert reply.status_code == 200, reply.text[:300]
    assert reply.json()["ok"] is True
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_the_validate_route_refuses_what_the_save_refuses(scope_dir, node_data,
                                                                clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reply = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                data={"_csrf_token": token_text, "_scope": SERVER_A,
                                      "schedule.00-12": "YYY"})
    assert reply.status_code == 200, reply.text[:300]
    body = reply.json()
    assert body["ok"] is False
    assert body["kind"] == "schema"
    assert digest(scope_dir) == before


@pytest.mark.asyncio
async def test_the_validate_route_refuses_the_same_scope_and_key_rules(scope_dir, node_data,
                                                                       clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reserved = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                   data={"_csrf_token": token_text, "_scope": "DEFAULT",
                                         "timezone": "UTC"})
        foreign = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                  data={"_csrf_token": token_text, "_scope": SERVER_A,
                                        "Host-B-Instance.timezone": "UTC"})
    assert reserved.json()["ok"] is False
    assert foreign.json()["ok"] is False


# ============================================= 5. the review step renders what the save writes

async def review_page(http, plan):
    """The review step as the operator sees it, with the whole plan in the address."""
    query = urlencode([("tab", "scheduler"), ("step", scheduler_plan.SCHEDULER_REVIEW_STEP)]
                      + [(field["name"], field["value"])
                         for field in scheduler_plan.form_fields(plan)])
    page = await http.get(f"/fleet/{SERVER_A}?{query}")
    assert page.status_code == 200, page.text[:400]
    return page.text


def review_keys(page: str) -> tuple[set, set]:
    return (set(re.findall(r'data-set="([^"]+)"', page)),
            set(re.findall(r'data-del="([^"]+)"', page)))


@pytest.mark.asyncio
async def test_the_review_step_renders_the_override_the_save_writes(scope_dir, node_data,
                                                                    clean_registry, monkeypatch):
    """ONE producer, two readers: the step's rows are exactly the keys the save changes."""
    plan = {"timezone": "Europe/Berlin", "schedule": {"00-12": "NNNNNNN"}}
    app = app_for(scope_dir, monkeypatch, plan=effective())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, plan)
        shown_set, shown_del = review_keys(page)
    assert shown_set == {"timezone", "schedule"}, page[:2000]
    assert shown_del == set()

    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, plan)
        token_text = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
        revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
        scope_name = re.search(r'name="_scope" value="([^"]*)"', page).group(1)
        # post the page's OWN hidden fields, exactly as a browser would
        hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
        hidden.update({"_csrf_token": token_text, "_revision": revision, "_scope": scope_name})
        reply = await save(http, hidden)
    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert set(block) == shown_set - {"timezone", "schedule"} | {"timezone", "schedule"}
    assert set(block) == shown_set
    assert not (shown_del & set(block))


@pytest.mark.asyncio
async def test_the_review_step_lists_a_key_it_will_delete(scope_dir, node_data, clean_registry,
                                                          monkeypatch):
    """The plan stops stating the schedule: the step says "back to inherited", and the save deletes it."""
    plan = {"timezone": "Europe/Berlin"}
    app = app_for(scope_dir, monkeypatch, plan=effective())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, plan)
        shown_set, shown_del = review_keys(page)
        hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
        reply = await save(http, hidden)
    assert shown_del == {"schedule"}
    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert set(block) == shown_set
    assert "schedule" not in block


# ================================== 6. the review step renders the sub-keys a save removes

#: server A's block carrying a warn ladder with a sound, a time and a countdown — the sub-keys a save
#: removes when the operator clears those controls.
WARN_BLOCK = ("  warn:\n"
              "    message: 'restarting soon'\n"
              "    sound: alarm.ogg\n"
              "    times:\n"
              "    - 600\n"
              "    countdown:\n"
              "      time: 30\n"
              "      message: 'bye'\n")


def prune_shown(page: str) -> dict:
    """The review step's prune rows: dotted group key -> the sub-keys it lists."""
    return {group: re.findall(r'class="mono">([^<]+)<', body)
            for group, body in re.findall(r'<div class="r" data-prune="([^"]+)">(.*?)</div>',
                                          page, re.S)}


def expected_prune(block, plan) -> dict:
    """The prune rows the step must show for *block* over *DEFAULT* with *plan*."""
    spec = scheduler_plan.minimal_override(DEFAULT_BLOCK, block, plan)
    deletes = set(spec["delete"])
    rows: dict = {}
    if "startup" not in deletes and spec["prune"]["startup"]:
        rows["startup"] = spec["prune"]["startup"]
    if "warn" not in deletes:
        if spec["prune"]["warn"]:
            rows["warn"] = spec["prune"]["warn"]
        if spec["prune"]["countdown"]:
            rows["warn.countdown"] = spec["prune"]["countdown"]
    if "action" not in deletes:
        for index, keys in enumerate(spec["prune"]["action"]):
            if keys:
                rows["action.%d" % index] = keys
    return rows


def hidden_of(page: str) -> dict:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))


async def review_page_with_markers(http, plan) -> str:
    """The review step rendered with the plan's shape markers, as the browser posts it back."""
    query = urlencode([("tab", "scheduler"), ("step", scheduler_plan.SCHEDULER_REVIEW_STEP)]
                      + [(field["name"], field["value"])
                         for field in (scheduler_plan.form_fields(plan)
                                       + scheduler_plan.shape_markers(plan))])
    page = await http.get(f"/fleet/{SERVER_A}?{query}")
    assert page.status_code == 200, page.text[:400]
    return page.text


@pytest.mark.asyncio
async def test_the_review_step_lists_the_sub_keys_the_save_removes(scope_dir, node_data,
                                                                   clean_registry, monkeypatch):
    """An entry switched to "act even with players on": the step names ``populated`` for that entry.

    The pin is against the override the save reads, not a hardcoded list: the step must list
    whatever ``minimal_override`` prunes, and the save must then remove exactly those sub-keys.
    """
    with_action(scope_dir)
    block = read_block(scope_dir, INSTANCE_A)
    plan = {"action": {"method": "restart"}}
    app = app_for(scope_dir, monkeypatch, plan=effective())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page_with_markers(http, plan)
        shown = prune_shown(page)
        reply = await save(http, hidden_of(page))
    assert shown == expected_prune(block, plan), page[:3000]
    assert shown == {"action.0": ["populated"]}, shown
    assert "Entry 1" in page, "a per-entry prune is labelled with its entry position"
    assert reply.status_code == 303, reply.text[:300]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert "populated" not in entry, "the listed deletion must be the one the save makes"
    assert entry["local_times"] == ["04:00"], "a key the plan never states survives"


@pytest.mark.asyncio
async def test_the_review_step_lists_a_cleared_warning_key_as_inherited(scope_dir, node_data,
                                                                       clean_registry, monkeypatch):
    """A cleared warn / countdown control: the step lists those sub-keys as returning to inherited."""
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace(
            "  schedule:\n    00-12: YYYYYYY",
            "  schedule:\n    00-12: YYYYYYY\n" + WARN_BLOCK),
        encoding="utf-8")
    block = read_block(scope_dir, INSTANCE_A)
    plan = {"timezone": "Europe/Berlin", "warn": {"message": "shutting down"}}
    app = app_for(scope_dir, monkeypatch, plan=effective())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page_with_markers(http, plan)
        shown = prune_shown(page)
        reply = await save(http, hidden_of(page))
    assert shown == expected_prune(block, plan), page[:3000]
    assert shown == {"warn": ["sound", "times", "countdown"],
                     "warn.countdown": ["time", "message"]}, shown
    assert reply.status_code == 303, reply.text[:300]
    warn = read_block(scope_dir, INSTANCE_A)["warn"]
    assert warn == {"message": "shutting down"}, block


@pytest.mark.asyncio
async def test_the_review_step_with_nothing_to_prune_is_unchanged(scope_dir, node_data,
                                                                  clean_registry, monkeypatch):
    """No prune spec: no prune row and no stray heading."""
    block = read_block(scope_dir, INSTANCE_A)
    plan = {"timezone": "Europe/Berlin", "schedule": {"00-12": "NNNNNNN"}}
    app = app_for(scope_dir, monkeypatch, plan=effective())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, plan)
        shown = prune_shown(page)
    assert expected_prune(block, plan) == {}
    assert shown == {}, page[:3000]
    assert "data-prune" not in page
    assert "What changes" in page


@pytest.mark.asyncio
async def test_an_empty_plan_for_a_fresh_server_creates_no_block(scope_dir, node_data,
                                                                 clean_registry, monkeypatch):
    """A server with no block of its own, saved with nothing stated, gets no block at all."""
    path = scheduler_path(scope_dir)
    path.write_text(text_of(scope_dir).split(f"{INSTANCE_B}:")[0], encoding="utf-8")
    digest_before = digest(scope_dir)
    app = build(scope_dir, cluster(), plugins=("scheduler",))
    monkeypatch.setattr(detail_page, "_scheduler",
                        lambda: FakeScheduler({"timezone": "UTC"}))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await csrf_of(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": "", "_scope": SERVER_B})
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_B) == {}, "an inherited server must not gain a block"
    assert digest(scope_dir) == digest_before, "an empty save leaves the file byte-identical"

# ============================ 7. a file that is not a mapping is refused, never a crash

#: the three documents that state no server blocks at all: one list item, a bare scalar, no content.
NOT_A_MAPPING_FILES = {"one-item list": "- a\n", "scalar": "hello\n", "empty": ""}

#: what the refusal says, at BOTH doors and in the validate verdict: one sentence, one wording.
NOT_A_MAPPING_SENTENCE = "scheduler.yaml is not a mapping of server blocks."


def notice_of(page: str) -> str:
    """The one-shot notice the page rendered after a redirect."""
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html.unescape(found.group(1))


async def refused_at(scope_dir, monkeypatch, door, document: str) -> tuple[int, str, bool, str]:
    """Post one save at *door* over *document* on disk, then the validate twin of the same file.

    The answer, its notice, whether the file moved, and the validate verdict's message — the four
    readings that say the two routes agree about this file.
    """
    scheduler_path(scope_dir).write_text(document, encoding="utf-8")
    before = digest(scope_dir)
    app = app_for(scope_dir, monkeypatch)
    data = {"_scope": SERVER_A, "_revision": ""}
    if door == "expert":
        data.update({"_expert": "1", "block_yaml": "timezone: UTC\n"})
    else:
        data.update({"timezone": "America/New_York"})
    async with client(app) as http:
        await sign_in(http, ADMIN)
        data["_csrf_token"] = await csrf_of(http)
        reply = await save(http, data)
        landed = await http.get(reply.headers["location"])
        verdict = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                  data={"_csrf_token": data["_csrf_token"], "_scope": SERVER_A,
                                        "timezone": "America/New_York"})
    return (reply.status_code, notice_of(landed.text), digest(scope_dir) != before,
            verdict.json()["message"])


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_mapping_is_refused_by_the_plan_door(
        scope_dir, node_data, clean_registry, monkeypatch):
    """A list, a scalar or an empty file: the plan save refuses and the file keeps its bytes.

    ``patch_config_file`` hands the mutation the round-trip document, and a list has no key a block
    could be written under — the save must answer the refusal the validate route already answers for
    the same file, not fail on the sequence's own indexing.
    """
    for label, document in NOT_A_MAPPING_FILES.items():
        status, notice, moved, verdict = await refused_at(scope_dir, monkeypatch, "plan", document)
        assert status == 303, f"{label}: {status}"
        assert not moved, f"{label}: a refused save rewrote the file"
        assert notice.startswith(settings_page.REFUSED_BANNER), f"{label}: {notice}"
        assert NOT_A_MAPPING_SENTENCE in notice, f"{label}: {notice}"
        assert verdict == NOT_A_MAPPING_SENTENCE, f"{label}: the validate twin disagrees: {verdict}"


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_mapping_is_refused_by_the_expert_door(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The same three files through Expert Mode: the block cannot be placed, so nothing is written."""
    for label, document in NOT_A_MAPPING_FILES.items():
        status, notice, moved, _verdict = await refused_at(scope_dir, monkeypatch, "expert", document)
        assert status == 303, f"{label}: {status}"
        assert not moved, f"{label}: a refused save rewrote the file"
        assert notice.startswith(settings_page.REFUSED_BANNER), f"{label}: {notice}"
        assert NOT_A_MAPPING_SENTENCE in notice, f"{label}: {notice}"


# ------------------------------- step 3: the save writes ONE trigger and the method's own keys

#: server A's block with an entry stating TWO triggers plus keys a restart does not use. It is the
#: state the bot ignores half of, and the one a switch must clean up.
TWO_TRIGGERS = ("  schedule:\n"
                "    00-12: YYYYYYY\n"
                "  action:\n"
                "    times:\n"
                "    - 04:00\n"
                "    - 08:00\n"
                "    cron: 0 5 * * *\n"
                "    max_mission_time: 600\n"
                "    password: hunter2\n"
                "    method: restart\n")


def with_two_triggers(scope_dir) -> None:
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace("  schedule:\n    00-12: YYYYYYY", TWO_TRIGGERS),
        encoding="utf-8")


#: the same entry written as a ONE-ITEM SEQUENCE, the shape the review helper's URL produces.
TWO_TRIGGERS_LIST = ("  schedule:\n"
                     "    00-12: YYYYYYY\n"
                     "  action:\n"
                     "  - times:\n"
                     "    - 04:00\n"
                     "    - 08:00\n"
                     "    cron: 0 5 * * *\n"
                     "    max_mission_time: 600\n"
                     "    password: hunter2\n"
                     "    method: restart\n")


def two_trigger_plan():
    return effective() | {"action": {"times": ["04:00", "08:00"], "cron": "0 5 * * *",
                                     "max_mission_time": 600, "password": "hunter2",
                                     "method": "restart"}}


@pytest.mark.asyncio
async def test_the_save_deletes_the_triggers_and_keys_the_plan_stops_stating(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The rendered step-3 form is the plan: what it no longer draws is deleted from the file."""
    with_two_triggers(scope_dir)
    app = app_for(scope_dir, monkeypatch, plan=two_trigger_plan())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, step="3")
        reply = await save_pairs(http, pairs)
    assert reply.status_code == 303, reply.text[:400]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["times"] == ["04:00", "08:00"], "the trigger the bot keeps is what is written"
    assert "cron" not in entry, "the second trigger leaves the file"
    assert "max_mission_time" not in entry, "the cap belongs to mission time and it is not chosen"
    assert entry["password"] == "hunter2", "a restart sets the password too, so it stays"
    assert entry["method"] == "restart"


@pytest.mark.asyncio
async def test_the_save_writes_the_times_its_rows_state(scope_dir, node_data, clean_registry,
                                                        monkeypatch):
    """One time per row, in row order — never one comma-separated value."""
    with_two_triggers(scope_dir)
    app = app_for(scope_dir, monkeypatch, plan=two_trigger_plan())
    changed = {"action_time.0.0": "21:45", "action_time.0.1": "06:00"}
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = [(name, changed.get(name, value))
                 for name, value in await step_form_pairs(http, step="3")]
        reply = await save_pairs(http, pairs)
    assert reply.status_code == 303, reply.text[:400]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["times"] == ["21:45", "06:00"]


@pytest.mark.asyncio
async def test_the_review_step_lists_the_trigger_a_switch_deletes(scope_dir, node_data,
                                                                  clean_registry, monkeypatch):
    """The review reads the same override the save writes, so the deletion is on the page first."""
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace("  schedule:\n    00-12: YYYYYYY", TWO_TRIGGERS_LIST),
        encoding="utf-8")
    plan = {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"},
            "action": [{"times": ["04:00", "08:00"], "method": "restart"}]}
    app = app_for(scope_dir, monkeypatch, plan=plan)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, plan)

    row = re.search(r'<div class="r" data-prune="action\.0">.*?</div>', page, re.S)
    assert row, page[:2000]
    for key in ("cron", "max_mission_time", "password"):
        assert key in row.group(0), (key, row.group(0))
    assert "Entry 1" in row.group(0)


# ------------------------------ step 3: the password, for the methods that set one

#: server A's block with ONE load entry that states no password: what a save must be able to add.
LOAD_NO_PASSWORD = ("  schedule:\n"
                    "    00-12: YYYYYYY\n"
                    "  action:\n"
                    "    times:\n"
                    "    - 04:00\n"
                    "    method: load\n"
                    "    mission_id: 3\n")

#: the same entry carrying a password, the shape a method switch must be able to clean up.
LOAD_WITH_PASSWORD = LOAD_NO_PASSWORD.replace("    mission_id: 3\n",
                                              "    mission_id: 3\n    password: hunter2\n")


def with_entry(scope_dir, block: str) -> None:
    """The scheduler file with *block* in place of server A's schedule."""
    scheduler_path(scope_dir).write_text(
        text_of(scope_dir).replace("  schedule:\n    00-12: YYYYYYY", block), encoding="utf-8")


def load_plan(password=None) -> dict:
    entry = {"times": ["04:00"], "method": "load", "mission_id": 3}
    if password is not None:
        entry["password"] = password
    return effective() | {"action": entry}


@pytest.mark.asyncio
async def test_a_password_the_load_states_survives_two_saves_byte_identically(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The one password control posts the value, the save writes it, and the next save leaves it be."""
    with_entry(scope_dir, LOAD_NO_PASSWORD)
    app = app_for(scope_dir, monkeypatch, plan=load_plan("hunter2"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, step="3")
        assert ("action.0.password", "hunter2") in pairs, "the step's own control posts the value"
        reply = await save_pairs(http, pairs)
        assert reply.status_code == 303, reply.text[:300]
        assert read_block(scope_dir, INSTANCE_A)["action"]["password"] == "hunter2"
        assert "password: hunter2" in text_of(scope_dir)
        # the page now renders the value the file states, and a second save changes nothing
        after = digest(scope_dir)
        follow_up = await step_form_pairs(http, step="3")
        assert ("action.0.password", "hunter2") in follow_up
        reply = await save_pairs(http, follow_up)
    assert reply.status_code == 303, reply.text[:300]
    assert digest(scope_dir) == after, "the carried password was rewritten by the second save"


def test_a_password_is_read_verbatim_never_trimmed():
    """The plan's reader takes the posted text as it stands: a password is compared byte for byte."""
    value = "  padded secret  "
    back = scheduler_plan.plan_from_form([("action.0.password", value)])
    assert back["action"][0]["password"] == value


@pytest.mark.parametrize("method", ("stop", "shutdown"))
@pytest.mark.asyncio
async def test_a_method_that_loads_no_mission_prunes_the_carried_password(
        scope_dir, node_data, clean_registry, monkeypatch, method):
    """Switching to stop/shutdown drops the key from the plan, so the save deletes it."""
    with_entry(scope_dir, LOAD_WITH_PASSWORD)
    app = app_for(scope_dir, monkeypatch, plan=load_plan("hunter2"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = [(name, method if name == "action.0.method" else value)
                 for name, value in await step_form_pairs(http, step="3")
                 if name != "action.0.password"]
        # the card for stop/shutdown draws no password control, so the browser posts none — every
        # other entry value still rides, which is what makes the pruning the method's doing
        assert ("action.0.method", method) in pairs
        reply = await save_pairs(http, pairs)
    assert reply.status_code == 303, reply.text[:300]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["method"] == method
    assert "password" not in entry, "the key the method no longer uses must leave the file"
    assert "password" not in text_of(scope_dir)


@pytest.mark.asyncio
async def test_the_saved_password_never_reaches_a_log_line_or_the_notice(
        scope_dir, node_data, clean_registry, monkeypatch, caplog):
    """The value is written and nothing else: no log record of this layer, no notice carries it.

    The schema validator is left out of the walk: it dump-logs the WHOLE candidate at DEBUG for
    every config file this install writes, so it names every secret those files already hold — a
    property of that logger, not of this step, and not something this module can silence.
    """
    with_entry(scope_dir, LOAD_NO_PASSWORD)
    app = app_for(scope_dir, monkeypatch, plan=load_plan("hunter2"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = await step_form_pairs(http, step="3")
        with caplog.at_level(logging.DEBUG):
            reply = await save_pairs(http, pairs)
            landed = await http.get(reply.headers["location"])
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A)["action"]["password"] == "hunter2"
    ours = [record for record in caplog.records if not record.name.startswith("pykwalify")]
    assert not [record for record in ours if "hunter2" in record.getMessage()], \
        "a log line carried the password"
    assert not [record for record in ours if "hunter2" in str(record.args)], \
        "a log record's arguments carried the password"
    assert "hunter2" not in notice_of(landed.text), "the notice carried the password"


# ------------------------- step 3: an entry whose only trigger is the deprecated pair

#: server A's block with an entry that states ONLY ``local_times`` — the shape the bot still reads
#: and this tab never writes.
LEGACY_ONLY = """  schedule:
    00-12: YYYYYYY
  action:
    local_times:
    - 04:00
    method: restart
"""

#: the same entry with a supported trigger BESIDE the deprecated key: editable, key kept.
LEGACY_AND_TIMES = """  schedule:
    00-12: YYYYYYY
  action:
    times:
    - 04:00
    local_times:
    - 05:00
    method: restart
"""

#: the line pair every one of those blocks replaces.
PLAIN_SCHEDULE = """  schedule:
    00-12: YYYYYYY"""


def with_legacy_entry(scope_dir, block: str) -> None:
    """The scheduler file carrying *block* where server A's plain schedule sits."""
    path = scheduler_path(scope_dir)
    before = path.read_text(encoding="utf-8")
    assert PLAIN_SCHEDULE in before
    path.write_text(before.replace(PLAIN_SCHEDULE, block), encoding="utf-8")


def legacy_only_plan():
    return effective() | {"action": {"local_times": ["04:00"], "method": "restart"}}


@pytest.mark.asyncio
async def test_a_save_of_a_legacy_only_entry_keeps_the_key_and_adds_no_trigger(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The step draws no trigger control, so the page's own form cannot add one — and the key stays.

    The rendered form is what is posted: if it carried a trigger radio or input the operator never
    chose, the save would write a second trigger the deprecated key then overrides.
    """
    with_legacy_entry(scope_dir, LEGACY_ONLY)
    app = app_for(scope_dir, monkeypatch, plan=legacy_only_plan())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=3")
        assert page.status_code == 200, page.text[:400]
        assert 'name="action_trigger.0"' not in page.text, "the step draws no trigger radio"
        pairs = form_pairs(page.text)
        trigger_names = [name for name, _value in pairs if name.startswith("action_trigger.")]
        assert not trigger_names, "a legacy-only entry must post no trigger choice"
        time_names = [name for name, _value in pairs if name.startswith("action_time.")]
        assert not time_names, "and no time row"
        reply = await save_pairs(http, pairs)
    assert reply.status_code == 303, reply.text[:400]
    text = text_of(scope_dir)
    assert """    local_times:
    - 04:00
""" in text, "the deprecated key is byte-identical"
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["local_times"] == ["04:00"]
    added = sorted(set(entry) & set(scheduler_plan.ACTION_TRIGGER_KEYS))
    assert not added, f"a trigger was added to a legacy-only entry: {added}"
    assert entry["method"] == "restart"


@pytest.mark.asyncio
async def test_a_save_of_an_entry_with_both_keeps_the_legacy_key_and_writes_its_trigger(
        scope_dir, node_data, clean_registry, monkeypatch):
    """A supported trigger beside the deprecated key: the trigger is edited, the key survives."""
    with_legacy_entry(scope_dir, LEGACY_AND_TIMES)
    plan = effective() | {"action": {"times": ["04:00"], "local_times": ["05:00"],
                                     "method": "restart"}}
    app = app_for(scope_dir, monkeypatch, plan=plan)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        pairs = [(name, "06:00" if name == "action_time.0.0" else value)
                 for name, value in await step_form_pairs(http, step="3")]
        reply = await save_pairs(http, pairs)
    assert reply.status_code == 303, reply.text[:400]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["times"] == ["06:00"], "the supported trigger is the one this step edits"
    assert entry["local_times"] == ["05:00"], "and the deprecated key survives untouched"


@pytest.mark.asyncio
async def test_the_review_step_lists_neither_deprecated_key_as_written_or_deleted(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The review's rows are the override: the pair is outside this tab's language, so no row.

    The save then leaves the key exactly where it was, which is what the page promised by not
    listing it — a row claiming the key would be rewritten would be the lie.
    """
    with_legacy_entry(scope_dir, LEGACY_ONLY)
    plan = {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"},
            "action": {"method": "restart"}}
    app = app_for(scope_dir, monkeypatch, plan=legacy_only_plan())
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page_with_markers(http, plan)
        shown_set, shown_del = review_keys(page)
        rows = re.findall(r'<div class="r" data-(?:set|del|prune)="[^"]*">(.*?)</div>', page, re.S)
        shown_prune = prune_shown(page)
        reply = await save(http, hidden_of(page))

    listed = " ".join(rows) + " " + " ".join(sorted(shown_set | shown_del))
    for key in scheduler_plan.NEVER_WRITTEN:
        assert key not in listed, (key, listed)
        assert key not in repr(shown_prune), key
    assert shown_set == {"timezone", "schedule", "action"}, shown_set
    assert shown_del == set()
    assert reply.status_code == 303, reply.text[:400]
    entry = read_block(scope_dir, INSTANCE_A)["action"]
    assert entry["local_times"] == ["04:00"], "the unlisted key is one the save leaves alone"
    assert not set(entry) & set(scheduler_plan.ACTION_TRIGGER_KEYS)


# ============================================ 8. the per-server scope on both write doors

async def review_credentials(http) -> tuple[str, str]:
    """The CSRF token and the revision the review step renders — both read from ITS form."""
    page = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=5")
    assert page.status_code == 200, page.text[:300]
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
    revision = re.search(r'name="_revision" value="([^"]*)"', page.text).group(1)
    return token, revision


@pytest.mark.asyncio
async def test_a_dcs_admin_save_writes_only_the_server_in_scope(scope_dir, node_data,
                                                                clean_registry, monkeypatch):
    """A DCS Admin writes its own server's block; the other block and DEFAULT stay identical."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token_text, revision = await review_credentials(http)
        reply = await save(http, {"_csrf_token": token_text, "_revision": revision,
                                  "_scope": SERVER_A, "timezone": "Europe/Berlin",
                                  "schedule.00-12": "NNNNNNN"})
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A)["schedule"] == {"00-12": "NNNNNNN"}
    assert read_block(scope_dir, INSTANCE_B) == INSTANCE_B_BLOCK
    assert read_block(scope_dir, "DEFAULT") == DEFAULT_BLOCK


@pytest.mark.asyncio
async def test_a_dcs_admin_review_step_renders_no_expert_panel(scope_dir, node_data,
                                                               clean_registry, monkeypatch):
    """The panel is absent for a writer who is not an Admin — never a disabled control."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await review_page(http, effective())
    assert "Expert Mode" not in page
    assert f'name="{scheduler_plan.BLOCK_YAML_FIELD}"' not in page
    assert 'id="sw-block-yaml"' not in page
    # the read-only block is still shown, so a writer sees what the save produces
    assert 'data-cfg-yaml-readonly' in page


@pytest.mark.asyncio
async def test_an_admin_review_step_keeps_the_expert_panel(scope_dir, node_data, clean_registry,
                                                           monkeypatch):
    """The same step for an Admin carries the toggle and the textarea the route reads."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, effective())
    assert "Expert Mode" in page
    assert f'name="{scheduler_plan.BLOCK_YAML_FIELD}"' in page


@pytest.mark.asyncio
async def test_the_validate_route_refuses_the_expert_block_from_a_dcs_admin(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The expert candidate is refused on the VALIDATE door too, and nothing is written."""
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token_text, _ = await review_credentials(http)
        reply = await http.post(settings_page.SCHEDULER_VALIDATE_PATH,
                                data={"_csrf_token": token_text, "_scope": SERVER_A,
                                      scheduler_plan.BLOCK_YAML_FIELD:
                                          "timezone: America/New_York\n"})
    assert reply.status_code == 200, reply.text[:300]
    body = reply.json()
    assert body["ok"] is False
    assert body["kind"] == "scope"
    assert digest(scope_dir) == before
