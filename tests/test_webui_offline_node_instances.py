"""An offline node's instance count comes from the declared ``instances:`` block.

``readmodels/nodes.py`` rendered an offline node's instance figure as ``... if node is not None else 0``,
but "no node" is exactly what an offline node is (``all_nodes[name] = None``) -- so a dead node read
**Instances 0** plus "No instances on this node" while its ``nodes.yaml`` declared those instances.
Pins: an offline node's count is what it DECLARES; a node declaring none still reads ``0`` with the
note; a live node's row is untouched; the declared names come from the in-process accessor read ONCE
at source build (a render issues no node call and does not re-read the YAML); the declared names are a
COUNT, not rows, so they never enter the Instances table. Runs in-process behind the provider seam."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice import readmodels
from services.webservice.scope import Scope
from test_webui_dashboard import BOT_YAML, _hash, _login

MASTER = "dcs-master-01"
DEAD = "dcs-dead-01"          # in nodes.yaml, declares instances, NOT registered -> None
EMPTY_NODE = "dcs-bare-01"    # in nodes.yaml, declares none, NOT registered -> None
DECLARED = (f"{DEAD}-1", f"{DEAD}-2", f"{DEAD}-3")


# ------------------------------------------------------------------------------ test doubles

def _instance(name, node, *, server=None, dcs_port=10309, webgui_port=8088):
    return types.SimpleNamespace(name=name, dcs_port=dcs_port, webgui_port=webgui_port,
                                 server=server, node=node)


def _server(name, node_name, *, players=(), managed_by=()):
    return types.SimpleNamespace(
        name=name, status="Running", node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=f"{name}-Instance"), port=10309,
        current_mission=None, players={p.ucid: p for p in players}, is_remote=False,
        locals=({"managed_by": list(managed_by)} if managed_by else {}))


def _node(name, *, master=False, instances=None):
    return types.SimpleNamespace(name=name, master=master, instances=dict(instances or {}),
                                 all_nodes=None)


class _Bot:
    """A bot stub exposing ONLY what ``resolve_source`` reads (``servers`` and ``node``)."""

    def __init__(self, servers, node):
        self.servers = {s.name: s for s in servers}
        self.node = node

    def __getattr__(self, item):  # pragma: no cover - a render must not reach past the seam
        raise AssertionError(f"touched bot.{item}")


class _NoRPCNode:
    """A resolved node that RAISES if anything but ``name``/``master``/``instances`` is read.

    ``all_nodes`` is allowed too, because ``resolve_source`` reads the cluster registry off the
    node it was handed (that is the seam, not a node call). Any OTHER attribute — a method such an
    RPC (``get_config``/``read_locals``/``find_all_instances``) or a property — trips the
    assertion, so "a render issues no node call" is proved by rendering rather than by reading.
    """

    ALLOWED = {"name", "master", "instances", "all_nodes"}

    def __init__(self, name, *, instances=()):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "master", False)
        object.__setattr__(self, "instances", dict(instances))

    def __getattr__(self, item):
        if item in self.ALLOWED:
            return object.__getattribute__(self, item) if item in self.__dict__ else None
        raise AssertionError(f"a render touched node.{item} - a node call on render ()")


def _secret(monkeypatch, mapping):
    """Patch the accessor and return the list of its calls (proves read-once at source build)."""
    calls: list[int] = []

    def _accessor():
        calls.append(1)
        return dict(mapping)

    monkeypatch.setattr(readmodels.source, "declared_node_instances", _accessor)
    return calls


def _resolve(monkeypatch, *, declared, entries, servers=(), node=None):
    _secret(monkeypatch, declared)
    node = node or _node(MASTER, master=True)
    node.all_nodes = dict(entries)
    return readmodels.resolve_source(node=node, bot=_Bot(servers, node))


# ------------------------------------------------------- the fallback (fail-before, then the fix)

def test_fail_before_an_offline_node_used_to_render_zero_then_the_note(monkeypatch):
    """The old expression ``... if node is not None else 0`` is what this replaces.

    With it, DEAD (a ``None`` entry) rendered ``Instances 0`` and the empty note. The declared
    block says it carries three instances, so 0 was a wrong count and the note a lie.
    """
 # the OLD rule, spelled out so the difference is on the record rather than remembered
    node = None
    old_value = (len(getattr(node, "instances", {}) or {}) if node is not None else 0)
    assert old_value == 0, "floor: the old expression reported 0 for an offline node"

    source = _resolve(monkeypatch, declared={DEAD: DECLARED}, entries={MASTER: None, DEAD: None})
    views = {view.name: view for view in readmodels.nodes(source)}
    assert views[DEAD].instances == len(DECLARED) == 3, \
        "the offline node must report the instances it declares, not 0"
    assert views[DEAD].online is False


def test_a_none_node_with_declared_instances_reports_the_declared_count(monkeypatch):
    source = _resolve(monkeypatch, declared={DEAD: DECLARED},
                      entries={MASTER: _node(MASTER, master=True), DEAD: None})
    view = {v.name: v for v in readmodels.nodes(source)}[DEAD]
    assert view.instances == 3
    assert view.online is False
 # nothing live is invented for the offline node
    assert view.servers == 0 and view.players == 0


def test_a_none_node_declaring_none_still_reads_zero(monkeypatch):
    """The truthful case: a node that really declares no instances reads 0 (the note follows)."""
    source = _resolve(monkeypatch, declared={}, entries={MASTER: None, EMPTY_NODE: None})
    view = {v.name: v for v in readmodels.nodes(source)}[EMPTY_NODE]
    assert view.instances == 0


def test_a_live_node_row_is_unchanged(monkeypatch):
    """A registered node's count is its OWN registry's length — the declared block never overrides."""
    live = _node(MASTER, master=True, instances={
        "A": _instance("A", None), "B": _instance("B", None)})
 # declared says something ELSE for the live node; it must be ignored for a live node
    source = _resolve(monkeypatch, declared={MASTER: ("X", "Y", "Z", "W")},
                      entries={MASTER: live})
    view = {v.name: v for v in readmodels.nodes(source)}[MASTER]
    assert view.instances == 2, "a live node reads its registry, not the declared block"
    assert view.online is True


def test_the_declared_count_follows_the_scope_node_filter(monkeypatch):
    """A scoped viewer sees a node only when it carries their server; the declared count follows."""
    host_b = _server("Host B Server", DEAD, managed_by=("Host B",))
    source = _resolve(monkeypatch, declared={DEAD: DECLARED, "other-node": ("o1",)},
                      entries={DEAD: None, "other-node": None}, servers=[host_b])
    scoped = readmodels.scoped_source(source, Scope.restricted({"Host B"}))
    names = {v.name for v in readmodels.nodes(scoped)}
    assert names == {DEAD}, "only the node carrying their server survives the scope"
    assert set(scoped.declared_instances) == {DEAD}, \
        "the declared map is filtered with the nodes, not left cluster-wide"


# --------------------------------------------- no node call, no per-render YAML read (the spy)

def test_a_render_issues_no_node_call_and_does_not_re_read_the_yaml(monkeypatch):
    """The accessor is called EXACTLY ONCE (source build) and never on a render, and a resolved
    node that forbids any call is never called."""
    calls = _secret(monkeypatch, {DEAD: DECLARED})
    node = _node(MASTER, master=True)
    live = _NoRPCNode("live-node", instances={"L": _instance("L", None)})
    node.all_nodes = {MASTER: node, DEAD: None, "live-node": live}

    source = readmodels.resolve_source(node=node, bot=_Bot((), node))
    assert len(calls) == 1, "the declared instances are read once, at source build"

 # renders: the read model and the whole overview, several times over
    for _ in range(3):
        readmodels.nodes(source)
        readmodels.overview(source)
    assert len(calls) == 1, "a render must not re-read the declared instances (no YAML read)"

 # and reading the live row through the read model did not call anything on the node
    views = {v.name: v for v in readmodels.nodes(source)}
    assert views["live-node"].instances == 1
    assert views[DEAD].instances == 3


# ---------------------------------------------------- the REAL accessor, end to end (no doubles)

def test_the_real_accessor_reads_the_declared_instances_from_a_nodes_yaml(tmp_path, monkeypatch):
    """``declared_node_instances`` reads the bot's OWN loaded copy of ``nodes.yaml``.

    ``core.utils.validators.get_node_data()`` parses it once and exposes ``.instances``. A temp config
    with real yaml is pointed at, the singleton is reset, and the whole path is exercised with NO
    monkeypatched accessor.
    """
    import types as _types

    import core.utils.validators as validators

    (tmp_path / "nodes.yaml").write_text(
        "dcs-master-01:\n"
        "  instances:\n"
        "    Master-Instance:\n"
        "      bot_port: 6666\n"
        "    Shared:\n"
        "      bot_port: 6667\n"
        "dcs-dead-01:\n"
        "  instances:\n"
        "    D-1:\n"
        "      bot_port: 6668\n"
        "    D-2:\n"
        "      bot_port: 6669\n"
        "dcs-bare-01:\n", encoding="utf-8")
    (tmp_path / "servers.yaml").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS",
                        _types.SimpleNamespace(config=str(tmp_path)))
    validators.NodeData._instance = None  # force a fresh read of THIS nodes.yaml
    try:
        declared = readmodels.declared_node_instances()
        assert declared == {"dcs-master-01": ("Master-Instance", "Shared"),
                            "dcs-dead-01": ("D-1", "D-2")}, declared
        assert "dcs-bare-01" not in declared, "a node that declares none is simply absent"

 #... and through resolve_source, with the REAL accessor (nothing patched):
        node = _node("dcs-master-01", master=True)
        node.all_nodes = {"dcs-master-01": node, "dcs-dead-01": None, "dcs-bare-01": None}
        source = readmodels.resolve_source(node=node, bot=_Bot((), node))
        views = {v.name: v for v in readmodels.nodes(source)}
        assert views["dcs-dead-01"].instances == 2, views["dcs-dead-01"]
        assert views["dcs-bare-01"].instances == 0, views["dcs-bare-01"]
    finally:
        validators.NodeData._instance = None  # do not leak this config into another test


# --------------------------------------------------------------------------- rendered evidence

def _build(config_dir, users):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config={"session": {"secret": "s" * 40, "cookie_name": "dcssb_ui",
                                      "max_age": 600},
                          "auth": {"public_base_url": "http://localhost:9876",
                                   "local": {"enabled": True, "users": users}}})
    return app


def _users():
    return [{"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _node_rows(html: str) -> dict:
    """The Nodes table's rows as ``{name: {"nums", "offline", "note"}}`` (reads what a reader sees)."""
    body = html.split("<tbody")[1].split("</tbody>")[0] if "<tbody" in html else ""
    rows: dict = {}
    for tr in body.split("<tr>")[1:]:
        match = re.search(r"<b>(.*?)</b>", tr)
        if not match:
            continue
        rows[match.group(1)] = {
            "nums": re.findall(r'<td class="num">(.*?)</td>', tr),
            "offline": ">offline<" in tr,
            "note": readmodels.NO_NODE_INSTANCES_MESSAGE in tr,
        }
    return rows


@pytest.mark.asyncio
async def test_browser_offline_node_rows_show_the_declared_count_without_the_empty_note(config_dir):
    """The rendered Nodes page: a dead node shows its declared count and no misleading note; a node
    that declares none shows 0 WITH the note; the live node is unchanged. And the declared
    instances are NOT leaked as Instances-table rows."""
    servers = [_server("Master Server", MASTER)]
    live_node = _node(MASTER, master=True, instances={
        "Master-Instance": _instance("Master-Instance", None, server=servers[0])})
    source = readmodels.LiveSource(
        servers=servers,
        instances=(_instance("Master-Instance", None, server=servers[0]),),
        nodes={MASTER: live_node, DEAD: None, EMPTY_NODE: None},
        node=live_node,
        declared_instances={DEAD: DECLARED})  # EMPTY_NODE absent -> declares none
    app = _build(config_dir, _users())
    app.state.webui_source_provider = lambda: source

    async with asgi_client(app) as client:
        await _login(client, "specialk")
        nodes_html = (await client.get("/nodes")).text
        instances_html = (await client.get("/instances")).text

    rows = _node_rows(nodes_html)
 # the dead node: the declared count, no empty note, still tagged offline
    assert rows[DEAD]["nums"][0] == "3", rows[DEAD]
    assert rows[DEAD]["offline"] is True
    assert rows[DEAD]["note"] is False, "an offline node with declared instances must not read empty"
 # the node that declares none: 0 WITH the note (the truthful case)
    assert rows[EMPTY_NODE]["nums"][0] == "0", rows[EMPTY_NODE]
    assert rows[EMPTY_NODE]["note"] is True
 # the live node: its own registry's count, unchanged, no note
    assert rows[MASTER]["nums"][0] == "1", rows[MASTER]
    assert rows[MASTER]["note"] is False
    print("NODES rows:", {k: v["nums"] for k, v in rows.items()})

 # the declared names are a COUNT, never rows: none of them appear on the Instances page
    for declared in DECLARED:
        assert declared not in instances_html, \
            "adds no Instances-table row for an offline node's declared instances"
    print("INSTANCES page contains no declared-but-offline rows:",
          all(d not in instances_html for d in DECLARED))
