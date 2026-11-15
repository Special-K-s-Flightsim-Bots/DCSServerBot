"""A node's DCS facts are POLLED and CACHED, never asked on a render.

The console must state, per node, the installed DCS branch/version and whether a newer build exists on
that branch. Those answers come from a node's own API (``get_dcs_branch_and_version`` and
``is_dcs_update_available``) — a local file/HTTP check on the node and an RPC on a remote one — so they
must never be issued from a page RENDER: every view would call every node and one slow node would stall
the page. ``services/webservice/dcs_update.py`` polls them in the background and caches the answer; the
read model reads ONLY the cache.

Whether a node runs DCS at all is the CONFIGURATION's fact: the node's ``DCS:`` block in ``nodes.yaml``,
kept by ``core.utils.validators.NodeData`` and carried to the render as ``source.declared_dcs`` —
never ``node.installation``, which raises on a proxy.

Pins: a node without a DCS block costs ZERO node calls; the poller's two calls per checked node; the
four triggers (first sweep at service start, a newcomer, the six-hour re-sweep, an immediate re-check);
the failure rule (UNKNOWN, logged once per transition, a last-known True kept, a last-known False
dropped); a render issues no node call and reads the cache; an offline node's facts come from the
declaration alone; and a node row without DCS data is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from services.webservice import dcs_update
from services.webservice import readmodels
from services.webservice.scope import Scope
from test_webui_frontend_switch import boot, cancel_tasks, run_start
from test_webui_offline_node_instances import (  # noqa: F401 - fixtures imported on purpose
    DEAD, DECLARED, EMPTY_NODE, MASTER, _build, _instance, _node_rows, _server, _users, config_dir,
)

BLOCK = {"installation": "C:\\DCS World", "autoupdate": True}


# ------------------------------------------------------------------------------ test doubles

class DcsNode:
    """A node double whose two DCS methods record every call and answer canned facts.

    ``calls`` is the spy: a render must leave it EMPTY, and a sweep must leave exactly two entries.
    """

    def __init__(self, name, *, master=False, branch="release", version="2.9.8.110", newer=None,
                 fail=False):
        self.name = name
        self.master = master
        self.instances = {}
        self.branch = branch
        self.version = version
        self.newer = newer
        self.fail = fail
        self.calls: list[str] = []

    async def get_dcs_branch_and_version(self):
        self.calls.append("get_dcs_branch_and_version")
        if self.fail:
            raise RuntimeError("the node is unreachable")
        return self.branch, self.version

    async def is_dcs_update_available(self):
        self.calls.append("is_dcs_update_available")
        if self.fail:
            raise RuntimeError("the node is unreachable")
        return self.newer


class PoisonNode(DcsNode):
    """A node whose DCS methods FAIL THE TEST if a render ever reaches them."""

    async def get_dcs_branch_and_version(self):
        raise AssertionError("a page render issued get_dcs_branch_and_version()")

    async def is_dcs_update_available(self):
        raise AssertionError("a page render issued is_dcs_update_available()")


class StrictNode:
    """A resolved node that RAISES if anything but the row's own three attributes is read.

    ``name`` / ``master`` / ``instances`` are what a node row reads; every other attribute (an RPC, a
    property such as ``installation``) trips the assertion, so "a render issues no node call" is proved
    by rendering rather than by reading the code.
    """

    ALLOWED = {"name", "master", "instances", "all_nodes"}

    def __init__(self, name, *, master=False):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "master", master)
        object.__setattr__(self, "instances", {})

    def __getattr__(self, item):
        if item in self.ALLOWED:
            return object.__getattribute__(self, item) if item in self.__dict__ else None
        raise AssertionError(f"a render touched node.{item} - a node call on render")


def _entry(checks, name):
    """The cache entry for *name*, asserting it exists — a missing entry is a failed pin."""
    record = checks.entry(name)
    assert record is not None, f"no cache entry for {name}"
    return record


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node`` (the service test boots
    the real config plumbing). The test interpreter's program is not a bot entry point, so the bot's
    parser never set it."""
    import core.commandline as commandline

    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


class _Bot:
    """A bot stub exposing ONLY what ``resolve_source`` reads (``servers`` and ``node``)."""

    def __init__(self, node, servers=()):
        self.node = node
        self.servers = {server.name: server for server in servers}

    def __getattr__(self, item):  # pragma: no cover - a render must not reach past the seam
        raise AssertionError(f"touched bot.{item}")


# ------------------------------------------------------- 1. a node without DCS costs no call

@pytest.mark.asyncio
async def test_a_node_without_a_dcs_block_costs_zero_node_calls():
    """The master-only case: the node is recorded as having no installation and is NEVER called."""
    checks = dcs_update.DcsChecks()
    poller = dcs_update.DcsUpdatePoller(checks)
    with_dcs = DcsNode("dcs-a-01")
    without = DcsNode("master-only")

    checked = await poller.tick({"dcs-a-01": with_dcs, "master-only": without},
                                declared={"dcs-a-01": dict(BLOCK)})

    assert checked == ("dcs-a-01",), "only the node that declares DCS is polled"
    assert without.calls == [], "a node with no DCS block must cost zero node calls"
    assert checks.has_dcs("master-only") is False
    assert checks.available("master-only") is None
    assert _entry(checks, "master-only").checked_at is None


@pytest.mark.asyncio
async def test_one_sweep_costs_two_calls_per_checked_node():
    """Per checked node: the branch/version call and the availability call — nothing else."""
    checks = dcs_update.DcsChecks()
    node = DcsNode("dcs-a-01", newer="2.9.9.111")

    assert await checks.check(node, declared=True) is True

    assert node.calls == ["get_dcs_branch_and_version", "is_dcs_update_available"]
    record = _entry(checks, "dcs-a-01")
    assert (record.has_dcs, record.branch, record.version) == (True, "release", "2.9.8.110")
    assert (record.latest, record.available) == ("2.9.9.111", True)
    assert record.checked_at is not None and record.checked_at.tzinfo is not None, \
        "checked_at must be an AWARE UTC timestamp"


@pytest.mark.asyncio
async def test_a_current_node_reports_its_installed_version_as_the_latest():
    """No newer build: ``available`` is False and ``latest`` is the installed version (not invented)."""
    checks = dcs_update.DcsChecks()
    await checks.check(DcsNode("dcs-a-01", newer=None), declared=True)
    record = _entry(checks, "dcs-a-01")
    assert record.available is False
    assert record.latest == record.version == "2.9.8.110"


# ------------------------------------------------------------------------ 2. the failure rule

@pytest.mark.asyncio
async def test_a_failed_check_is_unknown_and_logs_once(caplog):
    """A raising check → UNKNOWN (never \"up to date\"), logged ONCE PER TRANSITION."""
    checks = dcs_update.DcsChecks()
    with caplog.at_level(logging.WARNING, logger="services.webservice.dcs_update"):
        assert await checks.check(DcsNode("dcs-a-01", fail=True), declared=True) is None
    record = _entry(checks, "dcs-a-01")
    assert record.available is None
    assert record.checked_at is None
    assert [r for r in caplog.records if r.levelno == logging.WARNING] and \
        len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1, \
        "the first failure of a node logs exactly one warning"

 # the latch: the SAME node failing again is quiet until a success clears it
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="services.webservice.dcs_update"):
        await checks.check(DcsNode("dcs-a-01", fail=True), declared=True)
        await checks.check(DcsNode("dcs-a-01", fail=True), declared=True)
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == [], \
        "a node that is simply down must not spam the log once a minute"


@pytest.mark.asyncio
async def test_a_failed_check_keeps_a_known_true():
    """A transient failure must not forget a real update: the last known ``True`` survives."""
    checks = dcs_update.DcsChecks()
    checks.record("dcs-a-01", branch="release", version="2.9.8.110", latest="2.9.9.111",
                  available=True)
    assert await checks.check(DcsNode("dcs-a-01", fail=True), declared=True) is True
    assert checks.available("dcs-a-01") is True
    assert _entry(checks, "dcs-a-01").latest == "2.9.9.111", "the kept value keeps its facts"


@pytest.mark.asyncio
async def test_a_failed_check_drops_a_known_false_to_unknown():
    """The safe direction: a node that was current is not reported current after a failed check."""
    checks = dcs_update.DcsChecks()
    checks.record("dcs-a-01", branch="release", version="2.9.8.110", available=False)
    assert await checks.check(DcsNode("dcs-a-01", fail=True), declared=True) is None
    assert checks.available("dcs-a-01") is None
    assert _entry(checks, "dcs-a-01").checked_at is None


@pytest.mark.asyncio
async def test_the_installed_facts_survive_a_failed_availability_check():
    """The two calls fail independently: a version read successfully is not thrown away."""
    class HalfFailing(DcsNode):
        async def is_dcs_update_available(self):
            self.calls.append("is_dcs_update_available")
            raise RuntimeError("ED is unreachable")

    checks = dcs_update.DcsChecks()
    assert await checks.check(HalfFailing("dcs-a-01"), declared=True) is None
    record = _entry(checks, "dcs-a-01")
    assert record.available is None
    assert (record.branch, record.version) == ("release", "2.9.8.110")
    assert record.checked_at is None, "no availability was produced, so nothing is time-stamped"


# --------------------------------------------------------------------- 3. the poller's triggers

@pytest.mark.asyncio
async def test_the_first_tick_sweeps_every_node_and_the_six_hour_sweep_re_checks():
    """Trigger (a) service start = the first tick's full sweep; trigger (c) the six-hour re-sweep."""
    checks = dcs_update.DcsChecks()
    now = [0.0]
    poller = dcs_update.DcsUpdatePoller(checks, sweep_seconds=100, clock=lambda: now[0])
    a, b = DcsNode("a"), DcsNode("b")
    declared = {"a": dict(BLOCK), "b": dict(BLOCK)}

    assert await poller.tick({"a": a, "b": b}, declared) == ("a", "b")
    a.calls.clear(), b.calls.clear()

 # an ordinary tick well inside the window checks NEITHER (no newcomer, sweep not due)
    now[0] = 30
    assert await poller.tick({"a": a, "b": b}, declared) == ()
    assert a.calls == [] and b.calls == []

 # the six-hour sweep comes due: every node is re-checked
    now[0] = 100
    assert await poller.tick({"a": a, "b": b}, declared) == ("a", "b")
    assert a.calls == ["get_dcs_branch_and_version", "is_dcs_update_available"]


@pytest.mark.asyncio
async def test_a_newcomer_is_checked_and_an_unreachable_node_is_not():
    """Trigger (b): a node that comes ONLINE is a newcomer; a ``None`` entry is not."""
    checks = dcs_update.DcsChecks()
    poller = dcs_update.DcsUpdatePoller(checks)
    a = DcsNode("a")

    assert await poller.tick({"a": None}, {"a": dict(BLOCK)}) == ()
    assert checks.entry("a") is None, "an unreachable node is never checked"

    assert await poller.tick({"a": a}, {"a": dict(BLOCK)}) == ("a",)


@pytest.mark.asyncio
async def test_check_node_rechecks_one_node_immediately(monkeypatch):
    """Trigger (d): the seam a route uses right after a DCS update is accepted."""
    monkeypatch.setattr(dcs_update, "console_declared_dcs", lambda: {"dcs-a-01": dict(BLOCK)})
    node = DcsNode("dcs-a-01", newer="2.9.9.111")
    assert await dcs_update.check_node(node) is True
    assert dcs_update.available("dcs-a-01") is True
    assert node.calls == ["get_dcs_branch_and_version", "is_dcs_update_available"]


@pytest.mark.asyncio
async def test_check_node_makes_no_call_for_a_node_without_a_dcs_block(monkeypatch):
    """The configuration decides, on the one-node path too."""
    monkeypatch.setattr(dcs_update, "console_declared_dcs", lambda: {})
    node = DcsNode("master-only")
    assert await dcs_update.check_node(node) is None
    assert node.calls == []
    assert dcs_update.has_dcs("master-only") is False


# --------------------------------------------------------- 4. the render reads only the cache

def test_a_render_reads_the_cache_and_makes_no_node_call():
    """``node_views()`` builds the row from the DECLARED map + the cache — never from the node."""
    poison = PoisonNode("dcs-a-01", master=True)
    source = readmodels.LiveSource(nodes={"dcs-a-01": poison}, node=poison,
                                   declared_dcs={"dcs-a-01": dict(BLOCK)})

 # nothing checked yet: the record exists (the node declares DCS) and claims no version
    view = {row.name: row for row in readmodels.nodes(source)}["dcs-a-01"]
    assert view.dcs == readmodels.DcsView()
    assert view.dcs.available is None

 # the poller fills the cache; the render picks the answer up without touching the node
    dcs_update.record("dcs-a-01", branch="release", version="2.9.8.110", latest="2.9.9.111",
                      available=True)
    view = {row.name: row for row in readmodels.nodes(source)}["dcs-a-01"]
    assert (view.dcs.branch, view.dcs.version, view.dcs.latest) == \
        ("release", "2.9.8.110", "2.9.9.111")
    assert view.dcs.available is True
    assert view.dcs.checked_at is not None


def test_a_node_with_no_dcs_block_renders_no_dcs_record():
    """``None``, not an empty record: the row must be able to say "this node runs no DCS"."""
    strict = StrictNode("master-only")
    source = readmodels.LiveSource(nodes={"master-only": strict}, node=strict, declared_dcs={})
    view = {row.name: row for row in readmodels.nodes(source)}["master-only"]
    assert view.dcs is None


def test_the_render_never_reads_node_installation():
    """``node.installation`` raises on a proxy; the render answers DCS presence from the map alone."""
    strict = StrictNode("remote-01")
    source = readmodels.LiveSource(nodes={"remote-01": strict}, node=strict,
                                   declared_dcs={"remote-01": dict(BLOCK)})
    view = {row.name: row for row in readmodels.nodes(source)}["remote-01"]
    assert view.dcs is not None and view.dcs.version == ""


# --------------------------------------------------------------- 5. an offline node's row

def test_an_offline_node_reports_dcs_from_the_declaration_alone():
    """``all_nodes[name] is None``: the row still renders, and NOTHING is invented for it."""
 # a stale cache entry from when the node was up must not be presented as current
    dcs_update.record(DEAD, branch="release", version="2.9.8.110", latest="2.9.9.111",
                      available=True)
    source = readmodels.LiveSource(nodes={DEAD: None}, declared_instances={DEAD: DECLARED},
                                   declared_dcs={DEAD: dict(BLOCK)})
    view = {row.name: row for row in readmodels.nodes(source)}[DEAD]

    assert view.online is False
    assert view.instances == len(DECLARED)
    assert view.dcs is not None, "the configuration declares an installation, so the row says so"
    assert view.dcs.version == "" and view.dcs.latest == ""
    assert view.dcs.available is None, "an offline node is never reported as checked"
    assert view.dcs.checked_at is None


# ------------------------------------------------- 6. a node without DCS data is unchanged

@pytest.mark.asyncio
async def test_the_rendered_node_row_is_unchanged_when_no_dcs_data_exists(config_dir):
    """The figures, the ONLINE/OFFLINE tag and the declared instance count are byte-for-byte as before.

    No node declares a DCS block: every row's ``dcs`` is ``None``, and the rendered table must be the
    one the offline-count change already produced.
    """
    servers = [_server("Master Server", MASTER)]
    live_node = types.SimpleNamespace(
        name=MASTER, master=True, all_nodes=None,
        instances={"Master-Instance": _instance("Master-Instance", None, server=servers[0])})
    source = readmodels.LiveSource(
        servers=servers, instances=(_instance("Master-Instance", None, server=servers[0]),),
        nodes={MASTER: live_node, DEAD: None, EMPTY_NODE: None}, node=live_node,
        declared_instances={DEAD: DECLARED})
    app = _build(config_dir, _users())
    app.state.webui_source_provider = lambda: source

    from conftest import asgi_client
    from test_webui_dashboard import _login

    async with asgi_client(app) as client:
        await _login(client, "specialk")
        nodes_html = (await client.get("/nodes")).text

    rows = _node_rows(nodes_html)
    assert rows[DEAD]["nums"][0] == "3" and rows[DEAD]["offline"] is True
    assert rows[DEAD]["note"] is False
    assert rows[EMPTY_NODE]["nums"][0] == "0" and rows[EMPTY_NODE]["note"] is True
    assert rows[MASTER]["nums"][0] == "1" and rows[MASTER]["note"] is False
    assert all(view.dcs is None for view in readmodels.nodes(source)), \
        "a node with no DCS block carries no DCS record"


# --------------------------------------------------------------- 7. the declared DCS map

def _secret(monkeypatch, mapping):
    """Patch the accessor and return the list of its calls (proves read-once at source build)."""
    calls: list[int] = []

    def _accessor():
        calls.append(1)
        return dict(mapping)

    monkeypatch.setattr(readmodels.source, "declared_node_dcs", _accessor)
    return calls


def test_the_declared_dcs_map_is_read_once_at_source_build(monkeypatch):
    calls = _secret(monkeypatch, {DEAD: dict(BLOCK)})
    strict = StrictNode("live-node")
    master = types.SimpleNamespace(name=MASTER, master=True, instances={}, all_nodes=None)
    master.all_nodes = {MASTER: master, DEAD: None, "live-node": strict}

    source = readmodels.resolve_source(node=master, bot=_Bot(master))
    assert len(calls) == 1, "the declared DCS blocks are read once, at source build"
    assert set(source.declared_dcs) == {DEAD}

    for _ in range(3):
        readmodels.nodes(source)
        readmodels.overview(source)
    assert len(calls) == 1, "a render must not re-read the configuration (no YAML read)"


def test_the_declared_dcs_map_follows_the_scope_node_filter(monkeypatch):
    """A scoped viewer sees a node only when it carries their server; its DCS block follows."""
    host_b = _server("Host B Server", DEAD, managed_by=("Host B",))
    _secret(monkeypatch, {DEAD: dict(BLOCK), "other-node": dict(BLOCK)})
    master = types.SimpleNamespace(name=MASTER, master=True, instances={}, all_nodes=None)
    master.all_nodes = {DEAD: None, "other-node": None}
    source = readmodels.resolve_source(node=master, bot=_Bot(master, servers=[host_b]))
    scoped = readmodels.scoped_source(source, Scope.restricted({"Host B"}))
    assert set(scoped.declared_dcs) == {DEAD}, \
        "the declared DCS map is filtered with the nodes, not left cluster-wide"


# ---------------------------------------------------------- 8. the REAL configuration accessor

def _nodes_yaml(tmp_path):
    (tmp_path / "nodes.yaml").write_text(
        "dcs-a-01:\n"
        "  instances:\n"
        "    A-1:\n"
        "      bot_port: 6666\n"
        "  DCS:\n"
        "    installation: C:\\DCS World\n"
        "    autoupdate: true\n"
        "master-only:\n"
        "  instances:\n"
        "    M-1:\n"
        "      bot_port: 6667\n"
        "broken-01:\n"
        "  DCS: not-a-mapping\n"
        "null-01:\n"
        "  DCS:\n", encoding="utf-8")
    (tmp_path / "servers.yaml").write_text("{}\n", encoding="utf-8")


@pytest.fixture
def real_config(tmp_path, monkeypatch):
    """Point the ``NodeData`` singleton at a temp ``nodes.yaml`` and reset it afterwards."""
    import core.utils.validators as validators

    _nodes_yaml(tmp_path)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(config=str(tmp_path)))
    validators.NodeData._instance = None
    yield tmp_path
    validators.NodeData._instance = None


def test_node_data_keeps_each_nodes_dcs_block(real_config):
    """The blocks are read with the rest of ``nodes.yaml``; a node without one is simply absent."""
    import core.utils.validators as validators

    dcs = validators.get_node_data().dcs
    assert dcs["dcs-a-01"]["installation"] == "C:\\DCS World"
    assert dcs["dcs-a-01"]["autoupdate"] is True
    assert "master-only" not in dcs, "a node with no DCS block declares no installation"


def test_a_malformed_dcs_block_is_declared_but_empty(real_config):
    """A config oddity must never raise, and must never read as "no installation"."""
    import core.utils.validators as validators

    dcs = validators.get_node_data().dcs
    assert dcs["broken-01"] == {}, "a non-mapping value is declared, with nothing readable"
    assert dcs["null-01"] == {}, "an empty block is declared, with nothing readable"


def test_the_existing_unknown_error_is_unchanged(tmp_path, monkeypatch):
    """A missing ``nodes.yaml`` still raises the pykwalify ``UnknownError`` — the raises did not move."""
    import core.utils.validators as validators
    from pykwalify.errors import UnknownError

    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(config=str(tmp_path)))
    validators.NodeData._instance = None
    try:
        with pytest.raises(UnknownError):
            validators.get_node_data()
    finally:
        validators.NodeData._instance = None


def test_the_real_accessor_exposes_the_declared_dcs_map(real_config):
    """``declared_node_dcs`` reads the bot's OWN loaded copy — no file access of its own."""
    declared = readmodels.declared_node_dcs()
    assert set(declared) == {"dcs-a-01", "broken-01", "null-01"}, declared
    assert declared["dcs-a-01"]["installation"] == "C:\\DCS World"


# ------------------------------------------------------------- 9. the service starts the poll

@pytest.mark.asyncio
async def test_the_service_starts_the_dcs_poller(tmp_path, monkeypatch):
    """The cache is warm without anyone opening a page: the poll runs from service start."""
    ticks: list[int] = []

    async def spy_tick(self, nodes, declared=None):
        ticks.append(1)
        return ()

    monkeypatch.setattr(dcs_update.DcsUpdatePoller, "tick", spy_tick)

    off = boot(tmp_path / "off", frontend="false")
    await run_start(off)
    assert off.dcs_task is None, "frontend: false must not start the UI's DCS poller"
    await cancel_tasks(off)

    on = boot(tmp_path / "on", frontend="true")
    await run_start(on)
    assert on.dcs_task is not None, "the frontend starts its DCS poller beside the upgrade one"
    for _ in range(20):
        if ticks:
            break
        await asyncio.sleep(0.01)
    assert ticks, "the first tick is the service-start sweep"
    await cancel_tasks(on)
