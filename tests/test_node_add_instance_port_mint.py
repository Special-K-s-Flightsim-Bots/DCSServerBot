"""``add_instance``'s port mint: the read that decides the ports must agree with the write.

``NodeImpl.add_instance`` computes the next free ``bot_port``/``dcs_port``/``webgui_port`` by reading
``config/nodes.yaml`` and then writes the new instance into its node's block. The read happened
OUTSIDE the write's serialisation, so two adds landing together both minted from the same file state
and handed two instances the SAME ports — and the second write then clobbered the first one's block.

The write now carries the revision of the generation the mint was computed from
(``patch_config_file(..., expect_revision=...)``), which is the primitive's own anti-clobber guard: a
write whose revision has moved is REFUSED with ``StaleConfigRevision`` and writes nothing, so the
loser cannot overwrite the winner and cannot record a duplicate port. The caller retries against the
newer file and mints again.

These tests pin that contract with two real threads, synchronised so BOTH reads complete before
either write begins (the barrier sits in the YAML load ``add_instance`` performs):

* **the two adds cannot both land with the same ports** — exactly one writes, the other is refused;
* **the refused add wrote nothing** — the file carries one instance, not two, and no duplicate port;
* **sequential adds still mint increasing ports** — the guard refuses contention, it does not break
  the ordinary path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.instanceimpl import InstanceImpl
from core.data.impl.nodeimpl import NodeImpl
from core.utils.configwrite import StaleConfigRevision

#: TWO node blocks in the primitive's canonical style. The target block carries an instance with
#: ``bot_port 6666`` and the other node one with ``6667``, so the minted next port is observably
#: derived from the FILE (the double's ``all_nodes`` marks the target node inactive).
NODES_YAML = """\
# nodes.yaml for the cluster
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      dcs_port: 10308
      webgui_port: 8088
dcs-b-01:   # the second machine
  listen_address: 127.0.0.1
  probe_key: keep-me          # unrelated to instances
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6667
      dcs_port: 10318
      webgui_port: 8090
"""

TARGET_NODE = "dcs-a-01"
OTHER_NODE = "dcs-b-01"


# -------------------------------------------------------------------------------------- doubles

class _Bus:
    def __init__(self):
        self.servers: dict = {}
        self.sent: list = []

    async def send_init(self, server):
        self.sent.append(server)


class _Registry:
    """``ServiceRegistry`` double: one shared bus, no services to stop/start."""

    bus = _Bus()

    @classmethod
    def get(cls, t):
        return cls.bus


class _Factory:
    """``DataObjectFactory`` double: an instance that CARRIES the minted locals it was handed."""

    def __init__(self, saved_games: str):
        self.saved_games = saved_games
        self.instances: list = []

    def new(self, cls, **kwargs):
        if cls is InstanceImpl:
            instance = types.SimpleNamespace(
                name=kwargs["name"], locals=dict(kwargs["locals"]),
                home=os.path.join(self.saved_games, kwargs["name"]),
                node=kwargs.get("node"), server=None)
            instance.bot_port = instance.locals["bot_port"]
            instance.dcs_port = instance.locals["dcs_port"]
            instance.webgui_port = instance.locals["webgui_port"]
            self.instances.append(instance)
            return instance
        return types.SimpleNamespace(name="n/a", settings={})


class _Autoexec:
    def __init__(self, instance=None):
        self.instance = instance
        self.crash_report_mode = None
        self.net = None


def _block(text: str, node: str) -> str:
    found = re.search(rf"(?m)^{re.escape(node)}:[^\n]*\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node} in:\n{text}"
    return found.group(0)


def _read(config_dir: Path) -> str:
    return (config_dir / "nodes.yaml").read_text(encoding="utf-8")


def _ports(text: str) -> list[int]:
    return [int(m) for m in re.findall(r"(?m)^\s+bot_port:\s*(\d+)\s*$", text)]


@pytest.fixture
def install(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text("{}\n", encoding="utf-8")
    saved_games = tmp_path / "SavedGames"
    saved_games.mkdir()
    return config_dir, saved_games


def _node(monkeypatch, config_dir: Path, saved_games: Path) -> NodeImpl:
    """A real ``NodeImpl`` in its local shape; the instance-config collaborators are doubles.

    ``all_nodes`` maps the target node to ``None``: the node is not active yet, so the mint reads the
    on-disk block — the file state both concurrent adds must agree on.
    """
    monkeypatch.setattr(nodeimpl, "DataObjectFactory", lambda: _Factory(str(saved_games)))
    monkeypatch.setattr(nodeimpl, "ServiceRegistry", _Registry)
    monkeypatch.setattr(nodeimpl, "Autoexec", _Autoexec)
    monkeypatch.setattr(nodeimpl, "SAVED_GAMES", str(saved_games))

    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(config_dir)
    node._master = True
    node.locals = {}
    node.node = types.SimpleNamespace(name=TARGET_NODE, master=True)
    node.log = logging.getLogger("tests.node_port_mint")
    node.instances = {}
    node.all_nodes = {TARGET_NODE: None, OTHER_NODE: None}
    return node


class _BarrierYaml:
    """A YAML double whose ``load`` waits for both threads, so both reads finish before any write."""

    def __init__(self, yaml, barrier: threading.Barrier):
        self._yaml = yaml
        self._barrier = barrier

    def load(self, stream, *args, **kwargs):
        self._barrier.wait(timeout=10)
        return self._yaml.load(stream, *args, **kwargs)


def _hold_the_reads(monkeypatch, count: int) -> None:
    real_new_yaml = nodeimpl.new_yaml
    barrier = threading.Barrier(count)
    monkeypatch.setattr(nodeimpl, "new_yaml", lambda: _BarrierYaml(real_new_yaml(), barrier))


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. two adds landing together cannot both write the same ports

def test_two_concurrent_adds_cannot_mint_the_same_ports(install, monkeypatch):
    config_dir, saved_games = install
    node = _node(monkeypatch, config_dir, saved_games)
    _hold_the_reads(monkeypatch, 2)

    results: list[tuple[str, str, int | None]] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        try:
            instance = asyncio.run(node.add_instance(name))
            outcome = ("written", name, instance.bot_port)
        except StaleConfigRevision:
            outcome = ("refused", name, None)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("Alpha", "Bravo")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2, f"both adds must report an outcome, got {results}"
    written = [r for r in results if r[0] == "written"]
    refused = [r for r in results if r[0] == "refused"]
    assert len(written) == 1, f"exactly one add may land, got {results}"
    assert len(refused) == 1, f"the other must be refused with StaleConfigRevision, got {results}"

    after = _read(config_dir)
    ports = _ports(after)
    assert len(ports) == len(set(ports)), f"no two instances may share a bot_port: {ports}"
    landed = written[0][1]
    assert f"{landed}:" in _block(after, TARGET_NODE), "the winner's instance is really in the file"
    loser = refused[0][1]
    assert f"{loser}:" not in _block(after, TARGET_NODE), "the refused add wrote nothing"
    assert _block(after, OTHER_NODE) == _block(NODES_YAML, OTHER_NODE), \
        "the other node's block must not be re-serialised"


def test_a_refused_add_reports_which_file_moved(install, monkeypatch):
    """The refusal names the file and the two revisions, so a caller can retry against the new one."""
    config_dir, saved_games = install
    node = _node(monkeypatch, config_dir, saved_games)

    stale = nodeimpl.configwrite.config_revision(config_dir / "nodes.yaml")
    asyncio.run(node.add_instance("Alpha"))          # a writer lands between the read and the write
    with pytest.raises(StaleConfigRevision) as caught:
        nodeimpl.configwrite.patch_config_file(
            config_dir / "nodes.yaml", lambda document: None,
            schemas=None, expect_revision=stale)

    assert "nodes.yaml" in str(caught.value)
    assert caught.value.expected == stale
    assert caught.value.current != stale


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. the ordinary path: sequential adds mint increasing ports

def test_sequential_adds_mint_increasing_ports(install, monkeypatch):
    config_dir, saved_games = install
    node = _node(monkeypatch, config_dir, saved_games)

    first = asyncio.run(node.add_instance("Alpha"))
    second = asyncio.run(node.add_instance("Bravo"))

    assert first.bot_port != second.bot_port, "a second add must not reuse the first's ports"
    assert second.bot_port > first.bot_port, "and mints the next free port"
    after = _read(config_dir)
    assert f"bot_port: {first.bot_port}" in _block(after, TARGET_NODE)
    assert f"bot_port: {second.bot_port}" in _block(after, TARGET_NODE)
    assert _block(after, OTHER_NODE) == _block(NODES_YAML, OTHER_NODE)


def test_a_refused_add_can_retry_against_the_newer_file(install, monkeypatch):
    """The collision is detected and the loser retries — it never gets the winner's ports.

    A writer lands between this add's mint and its write (the wrapper inserts it through the same
    primitive, exactly as a concurrent add would). The first attempt must refuse, write nothing, and
    the retry must mint from the file the winner left behind.
    """
    config_dir, saved_games = install
    node = _node(monkeypatch, config_dir, saved_games)

    from core.utils import configwrite
    real_patch = configwrite.patch_config_file

    def patch_with_a_concurrent_winner(path, mutate, **kwargs):
        def winner(document):
            block = document[TARGET_NODE].setdefault("instances", {})
            block["Winner"] = {"home": "/home/a-winner", "bot_port": 6668}
        real_patch(path, winner, schemas=None)
        return real_patch(path, mutate, **kwargs)

    monkeypatch.setattr(configwrite, "patch_config_file", patch_with_a_concurrent_winner)
    with pytest.raises(StaleConfigRevision):
        asyncio.run(node.add_instance("Alpha"))
    monkeypatch.setattr(configwrite, "patch_config_file", real_patch)

    after_loser = _read(config_dir)
    assert "Alpha:" not in _block(after_loser, TARGET_NODE), "the refused add wrote nothing"

    retried = asyncio.run(node.add_instance("Alpha"))

    after = _read(config_dir)
    assert retried.bot_port != 6668, "the retry must not take the ports the winner already used"
    ports = _ports(after)
    assert len(ports) == len(set(ports)), f"no two instances may share a bot_port: {ports}"
    assert "Alpha:" in _block(after, TARGET_NODE) and "Winner:" in _block(after, TARGET_NODE)
    assert _block(after, OTHER_NODE) == _block(NODES_YAML, OTHER_NODE), \
        "the other node's block must not be re-serialised"
