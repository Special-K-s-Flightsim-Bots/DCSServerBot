"""The command line add-node flow on a FEDERATED install writes the node's OWN database.

THE RULE. ``main.yaml``'s ``database`` block is the CLUSTER database on a federated install
(``core.data.impl.nodeimpl.get_database_urls`` reads it as the cluster pool, password
``config/.secret/clusterdb.pkl``). ``install.py``'s node build used to write
``database_url if 'database' not in main else None``, so on a federated install — where ``main.yaml``
DOES carry a ``database`` block — the CLI-added node wrote NO block and INHERITED the federation. A
node database can never be the federation database, so on a federated install the node block CARRIES
its own URL and ``main.yaml``'s cluster database is never offered.

THE FEDERATION SIGNAL IS THE BOT'S OWN FILE — ``config/.secret/clusterdb.pkl``
(``install.is_federated``), the same file the web console's ``database.is_federated`` asks about. The
NON-federated convenience is untouched: there ``main.yaml``'s database IS the shared one and the node
inherits it (no ``database`` block), the installer's own rule.

WHERE THE ASSERTIONS SIT. Each test DRIVES ``Install.install`` — the real add-node flow, scripted
answers, a fake PostgreSQL driver — and pins the value the WRITTEN ``nodes.yaml`` node block receives,
so a future reorder of the condition cannot pass the test silently. The never-written set is pinned
by sha256 across a successful run.

No live PostgreSQL and no running bot are exercised here; nothing outside a temporary config dir is
touched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NEW_NODE = "NewNode"

#: ``main.yaml`` of a FEDERATED install: its own settings AND the cluster database under ``database``.
MAIN_CLUSTER = """\
guild_id: 123456789012345678
autoupdate: true
database:
  url: postgres://dcssb:SECRET@10.0.0.5:5432/clusterdb?sslmode=prefer
"""

#: ``nodes.yaml`` with NO node database — a federated add-node then has none to carry.
NODES_NO_URL = """\
NodeTwo:
  listen_port: 10043
  use_upnp: false
"""

#: ``nodes.yaml`` whose NodeTwo carries its OWN database — the URL a federated add-node must carry.
NODE_URL = "postgres://dcssb:SECRET@192.168.1.20:5432/nodedb?sslmode=prefer"
NODES_WITH_URL = """\
NodeTwo:
  listen_port: 10043
  use_upnp: false
  database:
    url: postgres://dcssb:SECRET@192.168.1.20:5432/nodedb?sslmode=prefer
"""

#: The files a CLI ADD-NODE must NEVER write, by path relative to the config dir.
NEVER_WRITTEN = ("main.yaml", "services/bot.yaml", "services/webservice.yaml",
                 "plugins/cloud.yaml", ".secret/token.pkl")

BOT_YAML = "owner: 1\nroles:\n  Admin:\n    - 2\n"
WEBSERVICE_YAML = "DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n"
CLOUD_YAML = "DEFAULT:\n  dcs-ban: false\n  discord-ban: false\n"


# ---------------------------------------------------------------------------- the harness

class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """A psycopg-shaped connection: answers the version probe, swallows the installer's DDL."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        text = str(query)
        self.statements.append(text)
        if "server_version_num" in text:
            return _Cursor(("16.2",))
        return _Cursor((None,))


class Answers:
    """Scripted prompt answers, consumed in order; a leftover answer is a test failure."""

    def __init__(self, seq):
        self.seq = list(seq)

    def take(self, prompt, kind):
        if not self.seq:
            raise AssertionError(f"ran out of answers at {kind}: {prompt!r}")
        return self.seq.pop(0)


def _load_install_module():
    spec = importlib.util.spec_from_file_location("install_federated_mod", REPO_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed(config_dir: Path, *, main_body: str, nodes_body: str, federated: bool) -> None:
    """A configured installation the add-node flow resumes from: main, nodes, bot, web, cloud, token."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "services").mkdir(exist_ok=True)
    (config_dir / "plugins").mkdir(exist_ok=True)
    (config_dir / ".secret").mkdir(exist_ok=True)
    (config_dir / "main.yaml").write_text(main_body, encoding="utf-8")
    (config_dir / "nodes.yaml").write_text(nodes_body, encoding="utf-8")
    (config_dir / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (config_dir / "services" / "webservice.yaml").write_text(WEBSERVICE_YAML, encoding="utf-8")
    (config_dir / "plugins" / "cloud.yaml").write_text(CLOUD_YAML, encoding="utf-8")
    (config_dir / ".secret" / "token.pkl").write_bytes(b"discord-token")
    if federated:
        (config_dir / ".secret" / "clusterdb.pkl").write_bytes(b"cluster-secret")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guarded(config_dir: Path) -> dict:
    """The never-written set as ``path -> sha256 or None``: an absent file must stay absent."""
    return {name: sha256(config_dir / name) if (config_dir / name).exists() else None
            for name in NEVER_WRITTEN}


def run_add_node(monkeypatch, tmp_path: Path, *, federated: bool, nodes_body: str,
                 main_body: str = MAIN_CLUSTER, node: str = NEW_NODE) -> tuple[Path, Path, dict]:
    """Drive ``Install.install`` for a NEW node on a seeded install; return ``(work, config, before)``."""
    work = tmp_path / "work"
    (work / "locale").mkdir(parents=True)
    config_dir = work / "config"
    seed(config_dir, main_body=main_body, nodes_body=nodes_body, federated=federated)
    before = guarded(config_dir)

    dcs = work / "dcs"
    (dcs / "Scripts").mkdir(parents=True)
    (dcs / "Scripts" / "MissionScripting.lua").write_text("sanitizeModule('io')\n", encoding="utf-8")

    module = _load_install_module()
    ans = Answers([
        "new",       # a new node (Prompt.ask, node not in nodes.yaml)
        "postgres",  # master database (Prompt.ask, via the provisioning IO)
        "postgres",  # master user
        "mpw",       # master password
        str(dcs),    # DCS installation (Prompt.ask, linux path)
        True,        # '1.2.3.4 is a static IP-address' (Confirm.ask)
        True,        # DGSA banlist for DCS players (Confirm.ask)
        True,        # DGSA banlist for Discord users (Confirm.ask)
        True,        # DCS auto-update (Confirm.ask)
        "",          # DCS-SRS installation path (Prompt.ask, empty = none)
    ])
    module.Prompt = type("P", (), {"ask": staticmethod(lambda prompt, **kw: ans.take(prompt, "Prompt"))})
    module.Confirm = type("C", (), {"ask": staticmethod(
        lambda prompt, default=None, **kw: ans.take(prompt, "Confirm"))})
    module.IntPrompt = type("I", (), {"ask": staticmethod(lambda prompt, **kw: ans.take(prompt, "IntPrompt"))})

    conn = FakeConn()
    monkeypatch.setattr(module.psycopg, "connect", lambda url, **kw: conn)
    monkeypatch.setattr(module.utils, "is_open", lambda ip, port, **kw: True)
    monkeypatch.setattr(module.utils, "is_upnp_available", lambda: False)
    monkeypatch.setattr(module.utils, "findDCSInstances", lambda *a, **k: {})
    # The win32 installation scan answers nothing, so step 2 falls back to the path prompt this file
    # scripts — the same flow on a bare Linux box and on a Windows one that has DCS installed.
    monkeypatch.setattr(module.utils, "detect_dcs_installation", lambda: [])
    # The SRS probe is ``os.path.expandvars('%ProgramFiles%\\DCS-SimpleRadio-Standalone')``: on a
    # Windows host that has SRS installed for real the prompt never fires and every later answer
    # shifts by one, so the variable is pinned to a directory that holds nothing.
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-srs"))

    async def _ip():
        return "1.2.3.4"
    monkeypatch.setattr(module.utils, "get_public_ip", _ip)

    cwd = os.getcwd()
    os.chdir(work)
    try:
        module.Install(node=node).install(config_dir="config", user="dcssb", database="dcssb")
    finally:
        os.chdir(cwd)

    assert not ans.seq, f"unused answers: {ans.seq}"
    return work, config_dir, before


def block_of(config_dir: Path, node: str) -> dict:
    from ruamel.yaml import YAML
    nodes = YAML().load((config_dir / "nodes.yaml").read_text(encoding="utf-8"))
    return nodes[node]


# ---------------------------------------------------------------------------- the tests

def test_federated_add_node_with_no_node_database_carries_its_own(monkeypatch, tmp_path):
    """Federated + no node database: the CLI prompts for one and the written block carries it."""
    from core.utils import db_setup
    _, config_dir, before = run_add_node(monkeypatch, tmp_path, federated=True,
                                         nodes_body=NODES_NO_URL)

    block = block_of(config_dir, NEW_NODE)
    # the node's OWN, freshly provisioned database — never main.yaml's cluster database
    expected = db_setup.config_connection_url("dcssb", "127.0.0.1", 5432, "dcssb")
    assert block["database"]["url"] == expected
    main_url = "postgres://dcssb:SECRET@10.0.0.5:5432/clusterdb?sslmode=prefer"
    assert block["database"]["url"] != main_url

    # a successful add leaves every never-written file byte-identical
    assert guarded(config_dir) == before


def test_federated_add_node_with_a_node_database_carries_that_url(monkeypatch, tmp_path):
    """Federated + a node database present: the written block carries the node's own URL."""
    _, config_dir, before = run_add_node(monkeypatch, tmp_path, federated=True,
                                         nodes_body=NODES_WITH_URL)

    block = block_of(config_dir, NEW_NODE)
    assert block["database"]["url"] == NODE_URL
    assert block["database"]["url"] != MAIN_CLUSTER.split("url: ")[1].strip()
    assert guarded(config_dir) == before


def test_non_federated_add_node_still_inherits_main_yamls_database(monkeypatch, tmp_path):
    """The deliberate omission SURVIVES: with no cluster secret the node writes no database block."""
    _, config_dir, before = run_add_node(monkeypatch, tmp_path, federated=False,
                                         nodes_body=NODES_WITH_URL)

    block = block_of(config_dir, NEW_NODE)
    assert "database" not in block
    assert guarded(config_dir) == before


def test_the_federation_signal_is_the_bots_own_file(monkeypatch, tmp_path):
    """``install.is_federated`` answers on ``.secret/clusterdb.pkl`` alone — the bot's own signal."""
    module = _load_install_module()
    config_dir = tmp_path / "config"
    (config_dir / ".secret").mkdir(parents=True)
    assert module.is_federated(str(config_dir)) is False
    (config_dir / ".secret" / "clusterdb.pkl").write_bytes(b"secret")
    assert module.is_federated(str(config_dir)) is True
    assert module.is_federated("") is False
