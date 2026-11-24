"""The joining database pre-fill: a node's own URL, never the federation's.

THE RULE. ``main.yaml``'s ``database.url`` is the CLUSTER database — the bot reads it as the cluster
pool (``nodeimpl.get_database_urls``: ``cpool_url = self.config['database']['url']``, password
``config/.secret/clusterdb.pkl``) and decides a federation by ``apool != cpool``. A node's OWN
database is ``nodes.yaml``'s per-node ``database.url``. A node database can therefore NEVER be the
federation database: the pre-fill prefers a node's URL, and on a federated install with no node URL
it stays empty rather than pointing the joining node at the federation.

THE FEDERATION SIGNAL IS THE BOT'S OWN. ``is_federated`` asks the question the bot asks — the
presence of ``config/.secret/clusterdb.pkl`` — not a re-derived one, so main.yaml's URL is offered
back ONLY when the install is not federated at all.
"""
from __future__ import annotations

from pathlib import Path

from services.webservice.setup import database as db_step
from services.webservice.setup import join as join_step
from services.webservice.setup.state import SetupDraft


CLUSTER_URL = "postgres://u:***@10.0.0.5:5432/db"
NODE_URL = "postgres://u:***@192.168.1.20:5432/db"
LOOPBACK_NODE_URL = "postgres://u:***@127.0.0.1:5432/db"


def federate(config_dir) -> None:
    """Mark *config_dir* federated with the bot's own signal: ``config/.secret/clusterdb.pkl``."""
    secret = Path(config_dir) / ".secret"
    secret.mkdir(parents=True, exist_ok=True)
    (secret / "clusterdb.pkl").write_bytes(b"secret")


def cluster_main(names_main_yaml: bool = True) -> dict:
    """``main.yaml`` as a federated install holds it: the cluster database under ``database.url``."""
    return {"guild_id": 7, "database": {"url": CLUSTER_URL}} if names_main_yaml else {"guild_id": 7}


# ------------------------------------------------------------------------------- the acceptance set

def test_a_federated_install_pre_fills_the_node_never_the_cluster(tmp_path):
    """The reported defect: a node block's own URL wins over main.yaml's cluster URL."""
    federate(tmp_path)
    nodes = {"NodeTwo": {"database": {"url": NODE_URL}}}
    assert db_step.cluster_database_url(cluster_main(), nodes, tmp_path) == NODE_URL


def test_a_federated_install_with_only_a_loopback_node_keeps_that_node_url(tmp_path):
    """Even a loopback node URL is the node's own — never main.yaml's cluster database."""
    federate(tmp_path)
    nodes = {"NodeOne": {"database": {"url": LOOPBACK_NODE_URL}}}
    assert db_step.cluster_database_url(cluster_main(), nodes, tmp_path) == LOOPBACK_NODE_URL


def test_a_federated_install_with_no_node_database_leaves_the_field_empty(tmp_path):
    """No node URL on a federated install: the field stays empty, never the cluster database."""
    federate(tmp_path)
    nodes = {"NodeOne": {"listen_port": 10042}, "NodeTwo": {"use_upnp": False}}
    assert db_step.cluster_database_url(cluster_main(), nodes, tmp_path) is None
    assert db_step.cluster_database_url(cluster_main(), {}, tmp_path) is None


def test_a_non_federated_install_still_uses_main_yaml(tmp_path):
    """The convenience that must survive: no cluster secret AND no node URL → main.yaml's URL."""
    nodes = {"NodeOne": {"listen_port": 10042}}
    assert db_step.cluster_database_url(cluster_main(), nodes, tmp_path) == CLUSTER_URL
    assert db_step.cluster_database_url(cluster_main(), {}, tmp_path) == CLUSTER_URL


def test_the_non_loopback_node_url_wins_over_a_loopback_one(tmp_path):
    """Among the nodes' own URLs the non-loopback one wins, in federated and bare installs alike."""
    nodes = {"NodeOne": {"database": {"url": LOOPBACK_NODE_URL}},
             "NodeTwo": {"database": {"url": NODE_URL}}}
    flipped = {"NodeTwo": nodes["NodeTwo"], "NodeOne": nodes["NodeOne"]}
    federate(tmp_path)
    assert db_step.cluster_database_url(cluster_main(), nodes, tmp_path) == NODE_URL
    assert db_step.cluster_database_url(cluster_main(), flipped, tmp_path) == NODE_URL
    bare = tmp_path / "bare"
    bare.mkdir()
    assert db_step.cluster_database_url(cluster_main(), nodes, bare) == NODE_URL
    assert db_step.cluster_database_url(cluster_main(), flipped, bare) == NODE_URL


# ------------------------------------------------------------- the signal itself, pinned

def test_the_federation_signal_is_the_cluster_secret_and_nothing_else(tmp_path):
    assert db_step.is_federated(tmp_path) is False
    federate(tmp_path)
    assert db_step.is_federated(tmp_path) is True
    # a main.yaml database block alone is NOT the signal on its own
    other = tmp_path / "main-only"
    other.mkdir()
    (other / "main.yaml").write_text("database:\n  url: " + CLUSTER_URL + "\n", encoding="utf-8")
    assert db_step.is_federated(other) is False


# --------------------------------------------------- the real flow: the first step's pre-fill

def seed_join_config(config_dir, *, with_main_database: bool, nodes_body: str) -> None:
    """A configured install: ``main.yaml`` (cluster db optional) and ``nodes.yaml`` as given."""
    config_dir = Path(config_dir)
    main = "guild_id: 7\n"
    if with_main_database:
        main += "database:\n  url: " + CLUSTER_URL + "\n"
    (config_dir / "main.yaml").write_text(main, encoding="utf-8")
    (config_dir / "nodes.yaml").write_text(nodes_body, encoding="utf-8")


def test_a_federated_join_pre_fills_the_draft_with_the_node_url(tmp_path):
    """The reported run, end to end: the database step opens on the NODE's database, not the cluster."""
    federate(tmp_path)
    seed_join_config(tmp_path, with_main_database=True,
                     nodes_body="NodeTwo:\n  database:\n    url: " + NODE_URL + "\n")
    draft = SetupDraft()
    join_step.seed_draft(draft, tmp_path)
    assert draft.database["path"] == db_step.EXISTING
    assert draft.database["host"] == "192.168.1.20"          # the node's, never 10.0.0.5
    assert draft.database["database"] == "db"


def test_a_federated_join_with_no_node_database_leaves_the_step_empty(tmp_path):
    """A federated install with no node URL: the draft carries no database block at all."""
    federate(tmp_path)
    seed_join_config(tmp_path, with_main_database=True, nodes_body="NodeTwo:\n  listen_port: 10043\n")
    draft = SetupDraft()
    join_step.seed_draft(draft, tmp_path)
    assert draft.database == {}


def test_a_non_federated_join_still_pre_fills_from_main_yaml(tmp_path):
    """No cluster secret, no node URL: main.yaml's URL is still the convenience that survives."""
    seed_join_config(tmp_path, with_main_database=True, nodes_body="NodeTwo:\n  listen_port: 10043\n")
    draft = SetupDraft()
    join_step.seed_draft(draft, tmp_path)
    assert draft.database["host"] == "10.0.0.5"

