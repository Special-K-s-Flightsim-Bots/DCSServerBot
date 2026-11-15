"""The Instances tab spans EVERY node's instances, not just the master's.

``readmodels.source.resolve_source`` built ``instances`` from ``node.instances`` (the webservice's OWN
node) while it built ``nodes`` from the whole cluster registry, so only the instances tab was wrong.
Pins: the source now reads every node in ``nodes`` (skipping an unresolved ``None``); each row is
attributed to ITS OWN node (instance names are unique per NODE, so two nodes may legally carry the
same name -- ``Instance.node.name`` wins over any source-level default); the counts span the cluster;
a scoped hoster sees an instance only when it is BOUND to one of their surviving servers. Runs
in-process against a stub two-node cluster."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice import readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.scope import Scope
from test_webui_dashboard import BOT_YAML, _hash, _login

MASTER = "dcs-master-01"
REMOTE = "dcs-b-01"


# ------------------------------------------------------------------------------ two-node doubles

def _instance(name, node, *, server=None, dcs_port=10309, webgui_port=8088):
    """An instance double that names its OWN node (``Instance.node.name`` is what the row reads)."""
    return types.SimpleNamespace(name=name, dcs_port=dcs_port, webgui_port=webgui_port,
                                 server=server, node=node)


def _server(name, node_name, managed_by=(), *, status="Running", players=()):
    locals_ = {"managed_by": list(managed_by)} if managed_by else {}
    return types.SimpleNamespace(
        name=name, status=status, node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=f"{name}-Instance"), port=10309,
        current_mission=None, players={p.ucid: p for p in players}, is_remote=False,
        locals=locals_)


def _player(name):
    return types.SimpleNamespace(name=name, ucid=name.lower(), active=True)


def _node(name, *, master=False, instances=None):
    node = types.SimpleNamespace(name=name, master=master, instances=dict(instances or {}),
                                 all_nodes=None)
    return node


class _Bot:
    """A bot stub exposing ONLY what ``resolve_source`` reads (``servers`` and ``node``)."""

    def __init__(self, servers, node):
        self.servers = {s.name: s for s in servers}
        self.node = node

    def __getattr__(self, item):  # pragma: no cover - a render must not reach past the seam
        raise AssertionError(f"touched bot.{item}")


class TwoNodeCluster:
    """The stub: a master node and a remote node, each carrying its own instances.

    Node A ("dcs-master-01") carries an instance bound to "Master Server" (managed by "Host A").
    Node B ("dcs-b-01") carries "Host B Server" (managed by "Host B"), an IDLE instance bound to no
    server, and — deliberately — an instance with the SAME NAME as one on node A ("Shared"), each
    bound to a server on its own node.
    """

    def __init__(self):
        self.master_server = _server("Master Server", MASTER, ["Host A"],
                                     players=[_player("Viper-1")])
        self.b_server = _server("Host B Server", REMOTE, ["Host B"], players=[_player("Viper-2")])
        self.servers = [self.master_server, self.b_server]

        self.master_node = _node(MASTER, master=True)
        self.remote_node = _node(REMOTE, master=False)

        self.master_shared = _instance("Shared", self.master_node, server=self.master_server)
        self.b_shared = _instance("Shared", self.remote_node, server=self.b_server)
        self.b_idle = _instance("B-Idle", self.remote_node, server=None)

        self.master_node.instances = {
            "Master-Instance": _instance("Master-Instance", self.master_node,
                                         server=self.master_server),
            "Shared": self.master_shared,
        }
        self.remote_node.instances = {
            "B-Instance": _instance("B-Instance", self.remote_node, server=self.b_server),
            "B-Idle": self.b_idle,
            "Shared": self.b_shared,
        }
 # the master's in-process view of the cluster: itself plus the resolved remote node
        self.master_node.all_nodes = {MASTER: self.master_node, REMOTE: self.remote_node}

    def source(self):
        return readmodels.resolve_source(node=self.master_node,
                                         bot=_Bot(self.servers, self.master_node))


# -------------------------------------------------------------------- the source spans the cluster

def test_the_source_reads_every_nodes_instances_not_just_the_masters():
    """FAIL-BEFORE: with the old code only "Master-Instance"/"Shared" (node A) rendered. Now both."""
    source = TwoNodeCluster().source()
    names = sorted(view.name for view in readmodels.instances(source))
    assert names == ["B-Idle", "B-Instance", "Master-Instance", "Shared", "Shared"]
    assert {view.node_name for view in readmodels.instances(source)} == {MASTER, REMOTE}


def test_each_row_is_attributed_to_its_own_node():
    """The SAME instance name on both nodes renders as TWO rows, one per node (not deduped, not
    mislabelled)."""
    source = TwoNodeCluster().source()
    shared = [view for view in readmodels.instances(source) if view.name == "Shared"]
    assert len(shared) == 2, "the same instance name on two nodes is two rows"
    assert {view.node_name for view in shared} == {MASTER, REMOTE}
 # and the binding is the row's OWN node's server, never the other node's
    by_node = {view.node_name: view.server_name for view in shared}
    assert by_node[MASTER] == "Master Server" and by_node[REMOTE] == "Host B Server"


def test_an_unresolved_node_contributes_no_rows_and_does_not_raise():
    """A ``{name: None}`` entry is a known-but-unresolved node: skipped for instances, still a node
    row that renders offline."""
    cluster = TwoNodeCluster()
    cluster.master_node.all_nodes = {MASTER: cluster.master_node, REMOTE: None}
    source = cluster.source()
 # no row from the unresolved node, and the surviving ones are the master's only
    assert sorted(view.name for view in readmodels.instances(source)) == \
        ["Master-Instance", "Shared"]
    nodes = {view.name: view for view in readmodels.nodes(source)}
    assert nodes[REMOTE].online is False, "the unresolved node still renders as an offline row"


def test_the_instance_count_and_tab_badge_span_the_cluster():
    """Point 4: a figure that was 'this node's instances' now counts every node's."""
    cluster = TwoNodeCluster()
    state = readmodels.overview(cluster.source())
    assert state.instance_count == 5, "the master-only figure was 2"
    badge = dashboard_page.tab_views(state, "instances")
    assert {tab["key"]: tab["count"] for tab in badge}["instances"] == 5


# ------------------------------------------------------------------------------ the scoped viewer

def _scoped(source, tokens):
    return readmodels.scoped_source(source, Scope.restricted(set(tokens)))


def test_a_scoped_hoster_sees_only_instances_bound_to_their_servers():
    """"Host B" sees the instance BOUND to their server; the idle one and the other node's stay out.
    Two nodes make this testable (the node B instances are bound to a Host B server)."""
    source = _scoped(TwoNodeCluster().source(), {"Host B"})
    views = readmodels.instances(source)
 # B-Instance and the Shared row ON NODE B (bound to Host B Server); NOT B-Idle, NOT Master rows
    assert sorted((view.name, view.node_name) for view in views) == \
        [("B-Instance", REMOTE), ("Shared", REMOTE)]
    assert readmodels.overview(source).instance_count == 2


def test_an_idle_instance_on_the_other_node_is_invisible_to_a_scoped_viewer():
    """The idle instance is cluster infrastructure, not "their server", so it is invisible to a scoped
    viewer."""
    source = _scoped(TwoNodeCluster().source(), {"Host B"})
    assert all(view.server_name for view in readmodels.instances(source)), \
        "an idle instance must be omitted for a scoped viewer"


def test_an_admin_sees_every_instance_on_every_node_unscoped():
    cluster = TwoNodeCluster()
    source = readmodels.scoped_source(cluster.source(), Scope.everything())
    assert source is not None
    assert readmodels.overview(source).instance_count == 5


# --------------------------------------------------------------------------- browser evidence

def _build(config_dir, users):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config={"session": {"secret": "s" * 40, "cookie_name": "dcssb_ui", "max_age": 600},
                          "auth": {"public_base_url": "http://localhost:9876",
                                   "local": {"enabled": True, "users": users}}})
    return app


def _users():
    return [{"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
            {"username": "hoster-b", "password_hash": _hash(), "roles": ["DCS Admin"],
             "scope": ["Host B"]}]


def _rows(html: str) -> list[tuple[str, str]]:
    """The ``(instance, node)`` pairs the Instances table actually listed.

    Reads the rendered rows of the tab body so the evidence is what a reader SEES, not a read-model
    value: each row is ``<b>name</b></td><td>node</td>`` in ``_table.html``'s instances branch.
    """
    body = html.split("<tbody")[1].split("</tbody>")[0] if "<tbody" in html else ""
    rows = re.findall(r"<tr><td><b>(.*?)</b></td><td>(.*?)</td>", body)
    return sorted(rows)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_browser_two_node_instances_tab_for_admin_and_hoster(config_dir):
    """The browser evidence: the two-node fleet's Instances page, as an Admin and as a hoster."""
    cluster = TwoNodeCluster()
    app = _build(config_dir, _users())
    app.state.webui_source_provider = cluster.source

 # --- the Admin: every instance on every node
    async with asgi_client(app) as client:
        await _login(client, "specialk")
        admin_html = (await client.get("/instances")).text
    admin_rows = _rows(admin_html)
    assert admin_rows == sorted([("B-Idle", REMOTE), ("B-Instance", REMOTE),
                                 ("Master-Instance", MASTER), ("Shared", MASTER),
                                 ("Shared", REMOTE)]), admin_rows
    print("ADMIN /instances rows:", admin_rows)

 # --- the hoster ("Host B"): only instances bound to their surviving server
    async with asgi_client(app) as client:
        await _login(client, "hoster-b")
        hoster_html = (await client.get("/instances")).text
    hoster_rows = _rows(hoster_html)
    assert hoster_rows == sorted([("B-Instance", REMOTE), ("Shared", REMOTE)]), hoster_rows
    print("HOSTER /instances rows:", hoster_rows)

 # and the cluster's own rows never leak into the hoster's page
    assert "Master-Instance" not in hoster_html
    assert "B-Idle" not in hoster_html
