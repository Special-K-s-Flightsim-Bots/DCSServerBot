"""A FEDERATED joining commit writes this node's OWN database — and refuses when it has none.

THE RULE (``main.yaml``'s ``database`` is the CLUSTER database on a federated install). The bot reads
``main.yaml``'s ``database.url`` as the cluster pool (``core.data.impl.nodeimpl.get_database_urls``,
password ``config/.secret/clusterdb.pkl``) and decides a federation by ``apool != cpool``. A joining
node that writes no ``database`` block therefore inherits the federation. A node database can never be
the federation database, so on a federated install the node block CARRIES its own URL, and a database
step that collected none REFUSES the commit rather than writing a block that would inherit.

THE FEDERATION SIGNAL IS THE BOT'S OWN — ``database.is_federated`` (``.secret/clusterdb.pkl``), the same
one the pre-fill fix uses. The NON-federated omission is untouched: there ``main.yaml``'s database IS
the shared one and the node inherits it by the CLI's own rule.

WHAT THESE DO NOT COVER: no browser, no running bot and no live PostgreSQL. ``install.py`` is read, never
written; the command line add-node path is reported, not fixed.
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
from services.webservice.setup import node as node_step
from services.webservice.setup import review as review_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.boot import JOIN
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
JOIN_NODE = "JoinNode"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')

#: ``main.yaml`` of a FEDERATED install: its own settings AND the cluster database under ``database``.
MAIN_CLUSTER = """\
# The installation's own settings.
guild_id: 123456789012345678
autoupdate: true
database:
  url: postgres://dcsserverbot:SECRET@10.0.0.5:5432/dcsserverbot?sslmode=prefer
"""

#: ``nodes.yaml`` whose NodeTwo carries its OWN database — the URL a federated join must write.
NODES_WITH_URL = """\
# Every node of this installation lives in this file.
NodeTwo:
  listen_port: 10043
  use_upnp: false
  database:
    url: postgres://dcsserverbot:SECRET@192.168.1.20:5432/dcsserverbot?sslmode=prefer
"""

#: ``nodes.yaml`` with NO node database at all — the federated step then collects nothing.
NODES_NO_URL = """\
# Every node of this installation lives in this file.
NodeTwo:
  listen_port: 10043
  use_upnp: false
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

NODE_URL = "postgres://dcsserverbot:SECRET@192.168.1.20:5432/dcsserverbot?sslmode=prefer"


# ------------------------------------------------------------------------------------- the fixture

def federate(config_dir) -> None:
    """Mark *config_dir* federated with the bot's OWN signal: ``config/.secret/clusterdb.pkl``."""
    secret = Path(config_dir) / ".secret"
    secret.mkdir(parents=True, exist_ok=True)
    (secret / "clusterdb.pkl").write_bytes(b"secret")


def seed_install(config_dir, *, nodes_body: str, main_body: str = MAIN_CLUSTER) -> None:
    """A configured, FEDERATED installation: ``main.yaml`` (cluster db), ``nodes.yaml``, bot, servers."""
    config_dir = Path(config_dir)
    (config_dir / "services").mkdir(parents=True, exist_ok=True)
    (config_dir / "plugins").mkdir(parents=True, exist_ok=True)
    (config_dir / "main.yaml").write_text(main_body, encoding="utf-8")
    (config_dir / "nodes.yaml").write_text(nodes_body, encoding="utf-8")
    (config_dir / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (config_dir / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    federate(config_dir)


#: Every file a joining commit must NEVER write, by path relative to the config dir.
NEVER_WRITTEN = ("main.yaml", "services/bot.yaml", "services/webservice.yaml",
                 "plugins/cloud.yaml", ".secret/token.pkl")


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guarded(config_dir) -> dict:
    """The never-written set as ``path -> sha256 or None``: an absent file must stay absent."""
    config_dir = Path(config_dir)
    return {name: sha256(config_dir / name) if (config_dir / name).exists() else None
            for name in NEVER_WRITTEN}


class _App:
    """The fact the commit reads off the application for federation: its recorded config directory."""

    def __init__(self, config_dir=None, node: str = JOIN_NODE) -> None:
        self.state = types.SimpleNamespace(webui_setup_node=node, webui_setup_mode=JOIN,
                                           webui_config_dir=str(config_dir) if config_dir else None)


def build(config_dir, *, node: str = JOIN_NODE, mode: str = JOIN):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    app = create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN,
                           node=node, mode=mode)
    app.state.setup_node_detection = node_step.Detection(
        candidates=(("DCS World (openbeta)", "/opt/dcs/two"),), public_ip=None, upnp=False)
    return app, window


async def enter(client):
    assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the screen carries no CSRF token"
    return found.group(1)


def joined_draft(*, database_host: str = "192.168.1.20") -> SetupDraft:
    """The draft as steps 3/4/8 would leave it, with the database password a passing Test holds."""
    draft = SetupDraft()
    draft.node = {"listen_port": 10044, "use_upnp": False, "dcs_choice": "0",
                  "dcs_installation": "/opt/dcs/two", "dcs_autoupdate": True}
    draft.servers = {"scanned": True, "detected": {"DCSserverTwo": "My Server"},
                     "rows": {"DCSserverTwo": {"manage": True, "name": "My Server", "status": "",
                                               "chat": "", "admin": "", "autostart": True}}}
    draft.database = {"path": "existing", "host": database_host, "port": 5432,
                      "master_database": "postgres", "master_user": "postgres",
                      "database": "dcsserverbot", "user": "dcsserverbot",
                      "user_password_attempts": 0}
    return draft


def held_password(app, password: str = "hunter2") -> None:
    db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] = password


# ---------------------------------------------------------------- the helper: write vs refuse

def test_a_federated_join_writes_the_nodes_own_database_block(tmp_path):
    """The reported defect one layer deeper: a federated node block CARRIES its own ``database.url``."""
    seed_install(tmp_path, nodes_body=NODES_WITH_URL)
    before = guarded(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft()

    plan = review_step.plan(draft, app=app, mode="join")
    report = review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    assert report.completed is True

    block = configwrite.load_config_file(tmp_path / "nodes.yaml")[JOIN_NODE]
    assert block["database"]["url"] == NODE_URL     # its OWN, never main.yaml's cluster database
    assert block["listen_port"] == 10044

    # the never-written set is byte-identical: only this node's block and its own rows landed
    assert guarded(tmp_path) == before


def test_a_federated_join_with_no_node_database_is_refused(tmp_path):
    """A federated install whose database step collected nothing: refuse, never inherit the cluster."""
    seed_install(tmp_path, nodes_body=NODES_NO_URL)
    before = guarded(tmp_path)
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft()
    draft.database = {}                 # the step collected nothing (federated pre-fill leaves it empty)

    with pytest.raises(review_step.JoinNodeDatabaseError) as raised:
        review_step.plan(draft, app=app, mode="join")
    assert "federated" in str(raised.value)
    assert "step 3" in str(raised.value)

    # nothing on disk changed — nothing was ever written
    after = guarded(tmp_path)
    assert after == before
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")


def test_a_non_federated_join_still_inherits_main_yamls_database(tmp_path):
    """The deliberate omission SURVIVES: with no cluster secret the node inherits main.yaml's db."""
    seed_install(tmp_path, nodes_body=NODES_WITH_URL)
    (tmp_path / ".secret" / "clusterdb.pkl").unlink()          # NOT federated
    app = _App(tmp_path)
    held_password(app)
    draft = joined_draft()

    plan = review_step.plan(draft, app=app, mode="join")
    review_step.write(plan, str(tmp_path), mode="join", closing=review_step.NODES)
    block = configwrite.load_config_file(tmp_path / "nodes.yaml")[JOIN_NODE]
    assert "database" not in block                              # inherited, exactly as the CLI leaves it


def test_the_refusal_helper_names_the_missing_piece(tmp_path):
    """``join_database_refusal`` is the non-raising form: a message, or ``None`` when it may proceed."""
    seed_install(tmp_path, nodes_body=NODES_NO_URL)
    app = _App(tmp_path)
    draft = joined_draft()
    draft.database = {}
    reason = review_step.join_database_refusal(draft, app=app)
    assert isinstance(reason, str) and "federated" in reason
    # a collected database answers, and a non-federated install is never refused
    assert review_step.join_database_refusal(joined_draft(), app=app) is None
    (tmp_path / ".secret" / "clusterdb.pkl").unlink()
    assert review_step.join_database_refusal(draft, app=app) is None


# --------------------------------------------------------------- the commit, driven end to end

async def _join_new(client):
    screen = await enter(client)
    token = token_of(screen.text)
    assert (await client.post(tour.STEP_PATH,
                              data={"_csrf_token": token, "action": "join_new"})).status_code == 303
    page = await client.get(SETUP_PATH)          # the first step render seeds the draft
    return token_of(page.text)


@pytest.mark.asyncio
async def test_the_federated_commit_writes_the_node_url_end_to_end(tmp_path):
    """Through the SHIPPED commit route: the node lands WITH its own database."""
    seed_install(tmp_path, nodes_body=NODES_WITH_URL)
    before = guarded(tmp_path)
    app, _ = build(tmp_path)
    held_password(app)
    async with asgi_client(app) as client:
        token = await _join_new(client)
        draft = SetupDraft.load(tmp_path)
        assert draft.database["host"] == "192.168.1.20"     # pre-filled with the NODE's database
        draft.step = 8
        draft.save(tmp_path)
        review = await client.get(tour.STEP_PATH, params={"n": "9"})
        done = await client.post(tour.STEP_PATH,
                                 data={"_csrf_token": token_of(review.text), "n": "9",
                                       "action": "write"})
    assert done.status_code == 200
    block = configwrite.load_config_file(tmp_path / "nodes.yaml")[JOIN_NODE]
    assert block["database"]["url"] == NODE_URL
    assert guarded(tmp_path) == before                      # the never-written set is untouched


@pytest.mark.asyncio
async def test_the_federated_commit_refuses_end_to_end_and_writes_nothing(tmp_path):
    """Through the SHIPPED commit route: no node database -> refused, no commit runs, no write.

    The commit is the POST itself (``submit_step`` does not re-seed, so the planted step-8 draft
    reaches step 9). The classified reason comes back and nothing on disk changes.
    """
    seed_install(tmp_path, nodes_body=NODES_NO_URL)
    before = guarded(tmp_path)
    app, _ = build(tmp_path)
    held_password(app)
    async with asgi_client(app) as client:
        token = await _join_new(client)
        draft = SetupDraft.load(tmp_path)
        assert draft.database == {}                         # the federated step collected nothing
        draft.step = 8                                      # the commit is the POST below
        draft.save(tmp_path)
        done = await client.post(tour.STEP_PATH,
                                 data={"_csrf_token": token, "n": "9", "action": "write"})
    assert done.status_code == 200
    assert "federated" in done.text                         # the classified reason is shown
    assert "Write configuration" not in done.text           # and no commit is offered
    after = guarded(tmp_path)
    assert after == before                                  # nothing on disk changed
    assert JOIN_NODE not in configwrite.load_config_file(tmp_path / "nodes.yaml")
