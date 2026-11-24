"""The joining state: ``main.yaml`` exists and THIS node has no block in ``nodes.yaml``.

Everything here is driven through the SHIPPED code — the shipped boot rule, the shipped rail, the
shipped first screen and the shipped commit — so what is asserted is the console's own behaviour and
not a description of it.

THE MERGE IS PINNED ON THE WRITE, NOT ON THE PLAN. The interesting claim is not "the plan says
``merge``"; it is that after the commit runs, every other node's block still says what it said and
``main.yaml`` and ``services/bot.yaml`` have not moved a byte. Those are the assertions below.

WHAT THESE DO NOT COVER: no browser, no running bot and no live PostgreSQL. Step 3's Test is never
run, so the commit is exercised with the answers a PASSING Test would have left in the draft — which
is exactly the state ``_submit_review`` reads. ``run.py`` is not imported either (its module import
pulls the bot's runtime environment), so its own branch is pinned on the two facts that branch reads
and on the three call sites that carry the node name.
"""
from __future__ import annotations

import hashlib
import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice.setup import database as db_step
from services.webservice.setup import join as join_step
from services.webservice.setup import node as node_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.boot import INSTALL, JOIN
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
JOIN_NODE = "JoinNode"
REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')

#: ``nodes.yaml`` as a real installation holds it: two other nodes, a comment, and both spellings of
#: the database host. NodeOne's URL is LOOPBACK (it names the machine that wrote it — here, this one)
#: and NodeTwo's is not, which is what the pre-fill must prefer.
NODES_YAML = """\
# Every node of this installation lives in this file.
NodeOne:
  listen_port: 10042
  use_upnp: false
  database:
    url: postgres://dcsserverbot:SECRET@127.0.0.1:5432/dcsserverbot?sslmode=prefer
  DCS:
    installation: /opt/dcs/one
  instances:
    DCSserverOne:
      bot_port: 6666
      home: /srv/saved/DCSserverOne

NodeTwo:
  listen_port: 10043
  use_upnp: false
  database:
    url: postgres://dcsserverbot:SECRET@192.168.1.20:5432/dcsserverbot?sslmode=prefer
"""

MAIN_YAML = """\
# The installation's own settings.
guild_id: 123456789012345678
autoupdate: true
"""

BOT_YAML = """\
# The guild's role mapping — install-wide, never this node's to write.
roles:
  Admin:
    - specialk
"""

SERVERS_YAML = """\
# Server rows of the whole installation.
OtherServer:
  channels:
    status: 111
    chat: 222
"""

SCHEDULER_YAML = """\
OtherInstance:
  schedule:
    '00-24': YYYYYYY
"""


# ------------------------------------------------------------------------------------- the fixture

def seed_install(config_dir) -> None:
    """A configured installation holding two other nodes, their bot mapping and their server rows."""
    config_dir = Path(config_dir)
    (config_dir / "services").mkdir(parents=True, exist_ok=True)
    (config_dir / "plugins").mkdir(parents=True, exist_ok=True)
    (config_dir / "main.yaml").write_text(MAIN_YAML, encoding="utf-8")
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (config_dir / "plugins" / "scheduler.yaml").write_text(SCHEDULER_YAML, encoding="utf-8")
    (config_dir / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _App:
    """The two facts the commit reads off the application: its node name and its secret stores.

    *config_dir* is the third, read only by the node-block parity rule (``main.yaml``'s cluster
    database): the real factory records it on the state, so a test that wants that rule seeds it here.
    """

    def __init__(self, config_dir=None, node: str = JOIN_NODE) -> None:
        self.state = types.SimpleNamespace(webui_setup_node=node, webui_setup_mode=JOIN,
                                           webui_config_dir=str(config_dir) if config_dir else None)


def build(config_dir, *, node: str = JOIN_NODE, mode: str = JOIN):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    app = create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN,
                           node=node, mode=mode)
    # step 4's detection is a network probe; the render reads the cache, so the tests seed it
    app.state.setup_node_detection = node_step.Detection(
        candidates=(("DCS World (openbeta)", "/opt/dcs/two"),), public_ip=None, upnp=False)
    return app, window


async def enter(client):
    assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the joining screen carries no CSRF token"
    return found.group(1)


def rail_labels(html: str) -> list[str]:
    """The rail's row labels ONLY — ``<span class="lbl">`` is also a field label outside the rail."""
    found = re.search(r'<details class="rail".*?</details>', html, re.S)
    return re.findall(r'<span class="lbl">([^<]*)</span>', found.group(0)) if found else []


def joined_draft(config_dir, *, database_host: str = "192.168.1.20") -> SetupDraft:
    """The draft as step 4/8 would leave it, with the database password a passing Test holds.

    The managed row carries PICKED channels: the joining step offers them the same way the first-run
    step does, and the commit writes what was picked (see ``test_webui_setup_join_channels``).
    """
    draft = SetupDraft()
    draft.node = {
        "listen_port": 10044,
        "use_upnp": False,
        "dcs_choice": "0",
        "dcs_installation": "/opt/dcs/two",
        "dcs_autoupdate": True,
    }
    draft.servers = {
        "scanned": True,
        "detected": {"DCSserverTwo": "My Server"},
        "rows": {"DCSserverTwo": {"manage": True, "name": "My Server", "status": "1234567890",
                                  "chat": "-1", "admin": "-1", "autostart": True}},
    }
    draft.database = {"path": "existing", "host": database_host, "port": 5432,
                      "master_database": "postgres", "master_user": "postgres",
                      "database": "dcsserverbot", "user": "dcsserverbot",
                      "user_password_attempts": 0}
    return draft


def held_password(app, password: str = "hunter2") -> None:
    db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] = password


# ------------------------------------------------------------------------------- the rail subset

def test_a_joined_instance_takes_a_bot_port_the_installation_does_not_already_use(tmp_path):
    """``NodeOne`` already holds 6666, so this node's instance must not take it.

    The installer's formula counts from 6666 and knows nothing about the instances the file carries,
    so two instances — one on each node — would share a bot port (the DCS web GUI's), which is the
    collision the rule exists for.
    """
    seed_install(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft(tmp_path)
    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    nodes = configwrite.load_config_file(tmp_path / "nodes.yaml")
    assert nodes[JOIN_NODE]["instances"]["DCSserverTwo"]["bot_port"] == 6667
    assert nodes["NodeOne"]["instances"]["DCSserverOne"]["bot_port"] == 6666      # untouched


@pytest.mark.asyncio
async def test_the_joining_step_shows_the_port_the_commit_writes(tmp_path):
    """The derived port on the page and the port in the file are the SAME number (one rule, read
    twice: by the step that renders it and by the commit that writes it)."""
    from test_webui_setup_join_channels import open_step_eight

    seed_install(tmp_path)
    _app, html = await open_step_eight(tmp_path)
    assert '<span class="val">6667</span>' in html
    assert '<span class="val">6666</span>' not in html


def test_the_joining_rail_is_the_same_ids_without_the_first_run_steps():
    assert tour.rail_steps(True, True, True, "join") == [3, 4, 8, 10]
    # row 8 obeys the same DCS/instance rule as on a first run
    assert tour.rail_steps(True, False, True, "join") == [3, 4, 10]
    assert tour.rail_steps(True, True, False, "join") == [3, 4, 10]
    # and the first-run rail is untouched, mode default included
    assert tour.rail_steps(True, True, True) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert tour.rail_steps(True, True, True, "install") == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_the_joining_progress_arithmetic_is_the_first_run_one(tmp_path):
    """A draft at 0 starts at the database step, and the steps off the rail are unreachable."""
    draft = SetupDraft()
    assert tour.current_step(draft, "join") == 3
    assert tour._previous_step(draft, 3, "join") is None      # nothing precedes it
    assert tour._previous_step(draft, 8, "join") == 4         # NOT the web console (7)
    assert tour._as_reachable_step("1", draft, 3, "join") == 3      # a first-run step is refused
    assert tour._as_reachable_step("4", draft, 3, "join") == 3      # ahead of the current one
    draft.step = 4
    assert tour.current_step(draft, "join") == 8


# ------------------------------------------------------------- the database pre-fill (pure)

def test_the_database_pre_fill_prefers_a_node_that_is_not_loopback(tmp_path):
    main = {"guild_id": 7}
    nodes = {"NodeOne": {"database": {"url": "postgres://u:***@127.0.0.1:5432/db"}},
             "NodeTwo": {"database": {"url": "postgres://u:***@192.168.1.20:5432/db"}}}
    assert db_step.cluster_database_url(main, nodes, tmp_path) == "postgres://u:***@192.168.1.20:5432/db"
    # reversed order: still the non-loopback one, whichever comes first in the file
    flipped = {"NodeTwo": nodes["NodeTwo"], "NodeOne": nodes["NodeOne"]}
    assert db_step.cluster_database_url(main, flipped, tmp_path) == "postgres://u:***@192.168.1.20:5432/db"


def test_a_node_url_outranks_main_yaml_even_when_main_yaml_names_one(tmp_path):
    main = {"database": {"url": "postgres://u:***@10.0.0.5:5432/db"}}
    nodes = {"NodeTwo": {"database": {"url": "postgres://u:***@192.168.1.20:5432/db"}}}
    # main.yaml's URL is the CLUSTER database; a joining node must never be pre-filled with it
    assert db_step.cluster_database_url(main, nodes, tmp_path) == "postgres://u:***@192.168.1.20:5432/db"


def test_a_single_machine_install_still_falls_back_to_its_loopback_node(tmp_path):
    main = {"guild_id": 7}
    nodes = {"NodeOne": {"database": {"url": "postgres://u:***@127.0.0.1:5432/db"}},
             "NodeTwo": {"listen_port": 10043}}
    assert db_step.cluster_database_url(main, nodes, tmp_path) == "postgres://u:***@127.0.0.1:5432/db"
    assert db_step.cluster_database_url(main, {}, tmp_path) is None
    assert db_step.cluster_database_url({}, {"NodeTwo": {"listen_port": 1}}, tmp_path) is None


def test_every_loopback_spelling_is_recognised():
    for host in ("127.0.0.1", "127.0.0.2", "127.1.2.3", "localhost", "::1"):
        assert db_step.is_loopback_host(host) is True, host
    for host in ("192.168.1.20", "db.example.com", "10.0.0.5", "", None):
        assert db_step.is_loopback_host(host) is False, host


def test_the_pre_fill_document_carries_every_field_but_the_password():
    url = "postgres://dcsss%2Fbot:SECRET@192.168.1.20:5433/dcs%20server?sslmode=prefer"
    document = db_step.prefill_document(url)
    assert document["path"] == db_step.EXISTING
    assert document["host"] == "192.168.1.20"
    assert document["port"] == 5433
    assert document["database"] == "dcs server"
    assert document["user"] == "dcsss/bot"
    assert "password" not in document and "bot_password" not in document
    # the answers it builds read back as those fields, on the EXISTING path
    answers = db_step.answers_from_mapping(document)
    assert answers.is_create is False and answers.host == "192.168.1.20"


def test_the_seed_runs_once_and_never_overwrites_an_answer(tmp_path):
    seed_install(tmp_path)
    draft = SetupDraft()
    join_step.seed_draft(draft, tmp_path)
    assert draft.database["path"] == db_step.EXISTING
    assert draft.database["host"] == "192.168.1.20"        # NOT NodeOne's 127.0.0.1
    assert draft.database["user"] == "dcsserverbot"
    assert draft.step == 0
    # a second call leaves an answer the operator has since changed exactly as it is
    draft.database["host"] = "10.10.10.10"
    join_step.seed_draft(draft, tmp_path)
    assert draft.database["host"] == "10.10.10.10"


# ------------------------------------------------------------------- the merge, pinned on the write

def test_the_commit_merges_this_node_and_leaves_every_other_file_alone(tmp_path):
    seed_install(tmp_path)
    before_nodes = configwrite.load_config_file(tmp_path / "nodes.yaml")
    before_servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    before_scheduler = configwrite.load_config_file(tmp_path / "plugins" / "scheduler.yaml")
    guarded = {name: sha256(tmp_path / name) for name in ("main.yaml", )}
    guarded["services/bot.yaml"] = sha256(tmp_path / "services" / "bot.yaml")

    app = _App()
    held_password(app)
    draft = joined_draft(tmp_path)

    plan = review_step.plan(draft, app=app, mode="join")
    keys = [item.target.key for item in plan]
    assert keys == ["database_password", "servers", "scheduler", "nodes"], keys
    for forbidden in ("main", "bot", "webservice", "cloud", "token", "discord_client_secret",
                      "clusterdb_password"):
        assert forbidden not in keys, forbidden

    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert [o.ok for o in report.outcomes] == [True, True, True, True], report.outcomes
    assert report.completed is True
    assert report.main_written is False       # nothing here writes main.yaml, and nothing may

    after_nodes = configwrite.load_config_file(tmp_path / "nodes.yaml")
    # 1. every OTHER node is exactly what it was
    assert after_nodes["NodeOne"] == before_nodes["NodeOne"]
    assert after_nodes["NodeTwo"] == before_nodes["NodeTwo"]
    assert set(after_nodes) == {"NodeOne", "NodeTwo", JOIN_NODE}
    # 2. the comments the file carries are still there
    text = (tmp_path / "nodes.yaml").read_text(encoding="utf-8")
    assert "# Every node of this installation lives in this file." in text
    assert "# The server rows of the whole installation." not in text
    # 3. THIS node's block is the one the installer's own builder produces
    assert after_nodes[JOIN_NODE]["listen_port"] == 10044
    assert after_nodes[JOIN_NODE]["database"]["url"] == \
        "postgres://dcsserverbot:SECRET@192.168.1.20:5432/dcsserverbot?sslmode=prefer"
    assert after_nodes[JOIN_NODE]["DCS"] == {"installation": "/opt/dcs/two", "autoupdate": True}
    assert list(after_nodes[JOIN_NODE]["instances"]) == ["DCSserverTwo"]

    after_servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    assert after_servers["OtherServer"] == before_servers["OtherServer"]
    # the row carries the PICKED channels: the joining step asks them (see
    # ``test_webui_setup_join_channels``), so the commit writes them
    assert after_servers["My Server"] == {"channels": {"status": 1234567890, "chat": -1,
                                                      "admin": -1}}
    assert (tmp_path / "servers.yaml").read_text(encoding="utf-8").startswith(
        "# Server rows of the whole installation.")

    after_scheduler = configwrite.load_config_file(tmp_path / "plugins" / "scheduler.yaml")
    assert after_scheduler["OtherInstance"] == before_scheduler["OtherInstance"]
    assert after_scheduler["DCSserverTwo"] == {"schedule": {"00-24": "YYYYYYY"}}

    # 4. the install-wide files never moved
    assert sha256(tmp_path / "main.yaml") == guarded["main.yaml"]
    assert sha256(tmp_path / "services" / "bot.yaml") == guarded["services/bot.yaml"]


def test_the_commit_adds_a_node_with_no_servers_at_all(tmp_path):
    """A joining machine with nothing to manage writes the node and no rows anywhere."""
    seed_install(tmp_path)
    app = _App()
    held_password(app)
    draft = joined_draft(tmp_path)
    draft.servers = {"scanned": True, "detected": {}, "rows": {}}

    plan = review_step.plan(draft, app=app, mode="join")
    assert [item.target.key for item in plan] == ["database_password", "servers", "nodes"]
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    assert configwrite.load_config_file(tmp_path / "servers.yaml") == {"OtherServer": {
        "channels": {"status": 111, "chat": 222}}}
    assert configwrite.load_config_file(tmp_path / "plugins" / "scheduler.yaml") == {
        "OtherInstance": {"schedule": {"00-24": "YYYYYYY"}}}


def test_a_whole_document_write_of_a_shared_file_is_refused_and_writes_nothing(tmp_path):
    """The guard at the write funnel: a plan that REPLACES nodes.yaml is refused, not obeyed."""
    seed_install(tmp_path)
    before = (tmp_path / "nodes.yaml").read_text(encoding="utf-8")
    plan = [review_step.PlannedWrite(target=review_step.NODES,
                                     document={"OnlyMe": {"listen_port": 1}})]
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is False
    assert report.outcomes[0].ok is False
    assert "merged" in report.outcomes[0].error
    assert (tmp_path / "nodes.yaml").read_text(encoding="utf-8") == before


def test_an_install_wide_target_is_refused_in_join_mode(tmp_path):
    """The same funnel refuses main.yaml, bot.yaml and every secret of the install."""
    seed_install(tmp_path)
    guarded = {name: sha256(tmp_path / name) for name in ("main.yaml", )}
    guarded["services/bot.yaml"] = sha256(tmp_path / "services" / "bot.yaml")
    plan = [review_step.PlannedWrite(target=review_step.NODES, document={JOIN_NODE: {}},
                                     merge=True),
            review_step.PlannedWrite(target=review_step.MAIN, document={"guild_id": 1}),
            review_step.PlannedWrite(target=review_step.BOT, document={"roles": {}}),
            review_step.PlannedWrite(target=review_step.WEBSERVICE, document={"DEFAULT": {}}),
            review_step.PlannedWrite(target=review_step.CLOUD, document={"DEFAULT": {}}),
            review_step.PlannedWrite(target=review_step.TOKEN_SECRET,
                                     secret=("token", "not-a-token")),
            review_step.PlannedWrite(target=review_step.CLUSTER_SECRET,
                                     secret=("clusterdb", "not-a-password"))]
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert [o.ok for o in report.outcomes] == [True] + [False] * 6
    for outcome in report.outcomes[1:]:
        assert "not written while joining" in outcome.error, outcome.target.key
    assert sha256(tmp_path / "main.yaml") == guarded["main.yaml"]
    assert sha256(tmp_path / "services" / "bot.yaml") == guarded["services/bot.yaml"]
    assert not (tmp_path / "services" / "webservice.yaml").exists()
    assert not (tmp_path / "plugins" / "cloud.yaml").exists()
    assert not (tmp_path / ".secret").exists()          # no token, no cluster password
    assert JOIN_NODE in configwrite.load_config_file(tmp_path / "nodes.yaml")


def test_the_rename_reuses_the_installers_own_config_rewrite(tmp_path):
    """The helper the flow calls really does rename the key in every yaml under the config dir."""
    seed_install(tmp_path)
    import install

    # database_only=False runs the helper's config half only — the DB half needs a live server
    install.Install.rename_node(str(tmp_path), "NodeOne", JOIN_NODE, database_only=False)
    nodes = configwrite.load_config_file(tmp_path / "nodes.yaml")
    assert JOIN_NODE in nodes and "NodeOne" not in nodes
    assert nodes[JOIN_NODE]["listen_port"] == 10042      # the block itself is carried over
    assert "NodeTwo" in nodes                            # and nothing else moved


def test_the_required_secrets_of_a_joining_commit_are_the_database_password_only():
    """No token is demanded: this installation's token is already on disk and never rewritten."""
    draft = joined_draft(Path("/nonexistent"))
    app = _App()
    assert review_step.required_secrets(draft, mode="join") == (review_step.DATABASE_SECRET,)
    assert [t.key for t in review_step.missing_secrets(draft, app=app, mode="join")] == \
        ["database_password"]
    held_password(app)
    assert review_step.missing_secrets(draft, app=app, mode="join") == []
    # the first-run rule is untouched: the token, plus the password on the existing path
    assert review_step.required_secrets(SetupDraft(), mode="install") == (review_step.TOKEN_SECRET,)
    assert review_step.required_secrets(SetupDraft(database={"path": "existing"}),
                                        mode="install") == (review_step.TOKEN_SECRET,
                                                            review_step.DATABASE_SECRET)


def test_the_commit_reports_where_it_stopped_when_a_later_file_is_refused(tmp_path, monkeypatch):
    """A refused servers.yaml leaves the node OUT of nodes.yaml and the window open."""
    seed_install(tmp_path)
    app = _App()
    held_password(app)
    draft = joined_draft(tmp_path)
    plan = review_step.plan(draft, app=app, mode="join")
    real = review_step._merge_one

    def refuse_servers(item, config_dir, **kwargs):
        if item.target is review_step.SERVERS:
            from services.webservice.setup.review import WriteOutcome
            return WriteOutcome(target=item.target, ok=False, error="refused for the test")
        return real(item, config_dir, **kwargs)

    monkeypatch.setattr(review_step, "_merge_one", refuse_servers)
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is False
    assert [o.skipped for o in report.outcomes][-1] is True       # nodes.yaml held back
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")


# ------------------------------------------------------------------------------ the first screen

@pytest.mark.asyncio
async def test_the_joining_console_asks_add_or_rename_before_anything_else(tmp_path):
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        assert "This node is not configured yet" in screen.text
        assert "NodeOne" in screen.text and "NodeTwo" in screen.text   # the rename choices
        assert rail_labels(screen.text) == []                          # no rail before the answer
        assert "A new node" in screen.text
        assert "An existing node whose hostname changed" in screen.text
        # a step URL renders the question too, never a step
        assert "This node is not configured yet" in (await client.get(tour.STEP_PATH)).text


@pytest.mark.asyncio
async def test_choosing_a_new_node_opens_the_rail_with_the_database_pre_filled(tmp_path):
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        moved = await client.post(tour.STEP_PATH,
                                  data={"_csrf_token": token_of(screen.text),
                                        "action": "join_new"})
        assert moved.status_code == 303
        step = (await client.get(SETUP_PATH)).text
    assert rail_labels(step) == ["Database", "This node", "DCS servers", "Review and write"]
    # step 3 is the "Use an existing database" path, already chosen and already filled
    assert 'name="path" value="existing" checked' in step
    assert 'name="host" value="192.168.1.20"' in step
    assert 'name="port" value="5432"' in step
    assert 'name="database" value="dcsserverbot"' in step
    assert "Federation support" not in step.split("</style>")[-1]  # the stylesheet mentions it
    assert 'name="federation"' not in step
    assert "DCSServerBot is not configured yet" not in step


@pytest.mark.asyncio
async def test_the_node_step_asks_no_banlist_questions_while_joining(tmp_path):
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        await client.post(tour.STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                                "action": "join_new"})
        await client.get(SETUP_PATH)        # the first step render seeds the draft
        draft = SetupDraft.load(tmp_path)
        draft.step = 3
        draft.save(tmp_path)
        step = (await client.get(tour.STEP_PATH, params={"n": "4"})).text
    assert "DCS World installation" in step
    assert "listen port" in step
    assert "autoupdate" in step
    assert "global banlist" not in step
    assert "dgsa" not in step
    assert "SRS" not in step


@pytest.mark.asyncio
async def test_the_rename_route_uses_the_installers_helper_and_ends_the_flow(tmp_path, monkeypatch):
    seed_install(tmp_path)
    calls: list[tuple] = []

    import install

    def fake_rename(config_dir, old_name, new_name):
        calls.append((str(config_dir), old_name, new_name))

    monkeypatch.setattr(install.Install, "rename_node", staticmethod(fake_rename))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        token = token_of(screen.text)
        # the confirmation is required
        refused = await client.post(tour.STEP_PATH,
                                    data={"_csrf_token": token, "action": "join_rename",
                                          "join_old_node": "NodeOne"})
        assert "Tick the confirmation" in refused.text and calls == []
        # and so is a node that exists
        refused = await client.post(tour.STEP_PATH,
                                    data={"_csrf_token": token, "action": "join_rename",
                                          "join_old_node": "NoSuchNode", "join_confirm": "on"})
        assert "Pick the node" in refused.text and calls == []
        done = await client.post(tour.STEP_PATH,
                                 data={"_csrf_token": token, "action": "join_rename",
                                       "join_old_node": "NodeOne", "join_confirm": "on"})
    assert calls == [(str(tmp_path), "NodeOne", JOIN_NODE)]
    assert done.status_code == 200
    assert f"Node NodeOne was renamed to {JOIN_NODE}." in done.text
    # the add path never ran: no node was merged and no route was recorded
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")
    assert "NodeOne" in configwrite.load_config_file(tmp_path / "nodes.yaml")
    assert join_step.route(SetupDraft.load(tmp_path)) is None


@pytest.mark.asyncio
async def test_the_add_route_writes_nothing_of_the_installation(tmp_path):
    """The first screen's answer is tour state only: no install-wide file moves."""
    seed_install(tmp_path)
    guarded = {}
    for name in ("main.yaml", "nodes.yaml", "servers.yaml", "services/bot.yaml",
                 "plugins/scheduler.yaml"):
        guarded[name] = sha256(tmp_path / name)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        await client.post(tour.STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                                "action": "join_new"})
        await client.get(SETUP_PATH)
    for name, before in guarded.items():
        assert sha256(tmp_path / name) == before, name


@pytest.mark.asyncio
async def test_a_joining_console_is_finished_the_moment_the_node_lands(tmp_path):
    """The window is the node's block: write it and the tour answers with the Done screen."""
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        screen = await enter(client)
        token = token_of(screen.text)
        await client.post(tour.STEP_PATH, data={"_csrf_token": token, "action": "join_new"})
        text = (tmp_path / "nodes.yaml").read_text(encoding="utf-8")
        (tmp_path / "nodes.yaml").write_text(
            text + "\nJoinNode:\n  listen_port: 10044\n  use_upnp: false\n", encoding="utf-8")
        done = (await client.get(SETUP_PATH)).text
        replay = await client.post(tour.STEP_PATH, data={"_csrf_token": token, "n": "3"})
    assert f"This node was added to config/nodes.yaml as {JOIN_NODE}." in done
    assert "Setup is done" in done
    assert rail_labels(done) == []
    assert replay.status_code == 200 and "Setup is done" in replay.text


# ------------------------------------------------------------- the launcher's own two branches

def test_a_no_web_setup_boot_in_the_joining_state_still_reaches_the_cli_installer(tmp_path):
    """``run.py``'s branch, pinned on the two facts it reads and on its three call sites.

    ``run.py`` is not importable in this suite (its module import pulls the bot's runtime
    environment), so the branch is asserted the way the branch reads it: ``setup_is_required`` is True
    for this node, and ``web_setup_enabled`` is False with ``--no-web-setup``. Those two leave the
    ``else: run_cmd_installer(...)`` arms of ``run.py`` as the only thing that can run — the CLI's
    own add-node/rename path, unchanged.
    """
    seed_install(tmp_path)
    from services.webservice.setup.boot import should_run_setup, web_setup_enabled

    assert should_run_setup(str(tmp_path), JOIN_NODE) is True
    assert web_setup_enabled(types.SimpleNamespace(no_web_setup=True, setup=False)) is False
    # and --setup in the same state opens the console instead of hitting run.py:444's error
    assert should_run_setup(str(tmp_path), JOIN_NODE) is True
    assert web_setup_enabled(types.SimpleNamespace(no_web_setup=False, setup=True)) is True

    source = (REPO_ROOT / "run.py").read_text(encoding="utf-8")
    assert source.count("setup_is_required(args.config, args.node)") == 3


def test_the_renamed_node_closes_the_window_for_its_old_name_too(tmp_path):
    """After the rename the OLD name is the one that must be refused, not the new one."""
    from services.webservice.setup.boot import should_run_setup

    seed_install(tmp_path)
    text = (tmp_path / "nodes.yaml").read_text(encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(text.replace("NodeOne:", "NodeOne:  # kept\n", 1),
                                         encoding="utf-8")
    assert should_run_setup(str(tmp_path), "NodeOne") is False
    assert should_run_setup(str(tmp_path), JOIN_NODE) is True


# ------------------------------------------- cross-node collisions: refuse, never overwrite

def colliding_draft(config_dir) -> SetupDraft:
    """A draft whose server and instance names collide with the other node's rows on disk.

    The server name ``OtherServer`` is the one ``servers.yaml`` already holds (with channels) and the
    instance name ``OtherInstance`` the one ``plugins/scheduler.yaml`` already holds (with an all-day
    schedule). The managed row keeps autostart OFF, so this node's scheduler entry (``{}``) DIFFERS
    from the file's schedule — a collision, not an idempotent re-run.
    """
    draft = joined_draft(config_dir)
    draft.servers = {
        "scanned": True,
        "detected": {"OtherInstance": "OtherServer"},
        "rows": {"OtherInstance": {"manage": True, "name": "OtherServer", "status": "",
                                   "chat": "", "admin": "", "autostart": False}},
    }
    return draft


def test_the_join_refuses_a_server_and_instance_name_another_node_owns(tmp_path):
    """The acceptance probe: a collision is refused, classified, and BOTH files stay byte-identical."""
    seed_install(tmp_path)
    before = {name: sha256(tmp_path / name) for name in
              ("nodes.yaml", "servers.yaml", "plugins/scheduler.yaml", "main.yaml")}
    before["services/bot.yaml"] = sha256(tmp_path / "services" / "bot.yaml")

    app = _App(tmp_path)
    held_password(app)
    draft = colliding_draft(tmp_path)

    plan = review_step.plan(draft, app=app, mode="join")
    assert [item.target.key for item in plan] == ["database_password", "servers", "scheduler", "nodes"]

    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    by_key = {outcome.target.key: outcome for outcome in report.outcomes}

    # (a) a refused, CLASSIFIED error naming each collision (and the file it lives in)
    assert by_key["servers"].ok is False
    assert "OtherServer" in by_key["servers"].error
    assert "servers.yaml" in by_key["servers"].error
    assert by_key["scheduler"].ok is False
    assert "OtherInstance" in by_key["scheduler"].error
    assert "scheduler.yaml" in by_key["scheduler"].error

    # (c) ok=False and no completion; the node is NOT added
    assert report.completed is False
    assert by_key["nodes"].skipped is True
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")

    # (b) BOTH files byte-identical afterwards — the other node's rows are untouched
    assert sha256(tmp_path / "servers.yaml") == before["servers.yaml"]
    assert sha256(tmp_path / "plugins" / "scheduler.yaml") == before["plugins/scheduler.yaml"]

    # and nothing install-wide moved
    assert sha256(tmp_path / "nodes.yaml") == before["nodes.yaml"]
    assert sha256(tmp_path / "main.yaml") == before["main.yaml"]
    assert sha256(tmp_path / "services" / "bot.yaml") == before["services/bot.yaml"]


def test_a_collision_free_join_still_succeeds(tmp_path):
    """The positive control: with no colliding key the same commit lands and adds only this node."""
    seed_install(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft(tmp_path)          # server "My Server", instance "DCSserverTwo": no collision

    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert [outcome.ok for outcome in report.outcomes] == [True, True, True, True]
    assert report.completed is True
    servers = configwrite.load_config_file(tmp_path / "servers.yaml")
    assert servers["OtherServer"] == {"channels": {"status": 111, "chat": 222}}
    assert servers["My Server"] == {"channels": {"status": 1234567890, "chat": -1, "admin": -1}}
    assert configwrite.load_config_file(tmp_path / "plugins" / "scheduler.yaml")["OtherInstance"] == \
        {"schedule": {"00-24": "YYYYYYY"}}
    assert JOIN_NODE in configwrite.load_config_file(tmp_path / "nodes.yaml")


def test_a_key_the_file_already_holds_with_the_same_value_is_allowed(tmp_path):
    """Only a DIFFERENT value is a collision: an identical row is an idempotent no-op."""
    seed_install(tmp_path)
    (tmp_path / "servers.yaml").write_text(
        "# Server rows of the whole installation.\nOtherServer:\n  channels:\n    status: 111\n"
        "    chat: 222\nMy Server: {}\n", encoding="utf-8")
    before = sha256(tmp_path / "servers.yaml")
    plan = [review_step.PlannedWrite(target=review_step.SERVERS,
                                     document={"My Server": {}}, merge=True)]
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.SERVERS)
    assert report.outcomes[0].ok is True
    assert report.outcomes[0].changed is False
    assert sha256(tmp_path / "servers.yaml") == before


# -------------------------------------------------- the listen port is derived from the cluster

def test_the_suggested_listen_port_comes_from_the_shared_nodes_yaml(tmp_path):
    """A node joining a cluster whose other node holds 10042 is NOT offered 10042."""
    (tmp_path / "nodes.yaml").write_text("OtherNode:\n  listen_port: 10042\n", encoding="utf-8")
    port = tour.suggested_listen_port(tmp_path)
    assert port != 10042
    # and it is exactly what the installer's own formula seeds, advanced past any bound port
    assert port == node_step.next_free_listen_port([{"listen_port": 10042}])
    assert port >= 10043


def test_the_suggested_listen_port_with_no_nodes_yaml_is_the_installer_default(tmp_path):
    assert tour.suggested_listen_port(tmp_path) == node_step.next_free_listen_port([])
    assert tour.suggested_listen_port(tmp_path) >= node_step.DEFAULT_LISTEN_PORT


# --------------------------------------------------- the node block's database.url parity

def test_a_joining_node_writes_no_database_block_when_main_yaml_owns_one(tmp_path):
    """Parity with `install.py`: main.yaml's cluster database is inherited, not repeated per node."""
    seed_install(tmp_path)
    (tmp_path / "main.yaml").write_text(
        MAIN_YAML + "database:\n  url: postgres://dcsserverbot:SECRET@10.0.0.5:5432/dcsserverbot"
                    "?sslmode=prefer\n", encoding="utf-8")
    main_before = sha256(tmp_path / "main.yaml")

    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft(tmp_path)

    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True
    block = configwrite.load_config_file(tmp_path / "nodes.yaml")[JOIN_NODE]
    assert "database" not in block            # inherited from main.yaml, exactly as the CLI leaves it
    assert block["listen_port"] == 10044
    assert sha256(tmp_path / "main.yaml") == main_before    # and main.yaml is never written


def test_a_joining_node_writes_its_own_url_when_main_yaml_names_no_database(tmp_path):
    """The other half of the rule: with no cluster database in main.yaml the node carries its URL."""
    seed_install(tmp_path)                    # MAIN_YAML has no database block
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft(tmp_path)

    plan = review_step.plan(draft, app=app, mode="join")
    review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    block = configwrite.load_config_file(tmp_path / "nodes.yaml")[JOIN_NODE]
    assert block["database"]["url"] == \
        "postgres://dcsserverbot:SECRET@192.168.1.20:5432/dcsserverbot?sslmode=prefer"
