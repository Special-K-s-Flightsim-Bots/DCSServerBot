"""``nodes.yaml`` whole-document writers: ``NodeImpl.set_config`` and ``NodeImpl.init_instances``.

``config/nodes.yaml`` is cluster-wide — one top-level block per node. Both writers used to load the
whole file and dump it back through the bare emitter, so every OTHER node's block came back
re-serialised (a hand-maintained sequence re-indented, a long scalar re-folded) and the file was read
long before it was written (a concurrent writer's change in between was silently lost).

Both now write through ``configwrite.patch_config_file`` with a mutate scoped to THIS node's block:

* **only the target node's block changes** — every other top-level node key is BYTE-identical after
  the call, its comments and its unrelated key included. The fixture is in the canonical style the
  primitive serialises, so nothing but the intended change may differ;
* **the change really landed** — the merged keys / the stamped ports are in the target block;
* **a key the writer assumes and cannot find is refused loudly** — ``ConfigWriteError`` with the file
  left byte-identical, instead of a bare ``KeyError`` or a silently skipped rewrite;
* **``init_instances`` still writes nothing when nothing needs stamping** — the primitive's no-op rule
  keeps the file (and its ``.bak``) exactly as it is, which is what the old ``dirty`` flag did.
"""
from __future__ import annotations

import logging
import re
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.utils import configwrite
from core.utils.configwrite import ConfigWriteError

REPO_ROOT = Path(__file__).resolve().parents[1]

#: TWO node blocks in the primitive's canonical style (mapping=2, sequence=4, offset=2) — verified to
#: re-serialise byte-for-byte under ``configwrite``, which is what makes "the other block is
#: byte-identical" a real assertion. The second block carries a trailing comment on its own line AND
#: an unrelated key (``probe_key``) with its own comment, plus a SEQUENCE: a bare-emitter re-dump
#: re-indents that sequence to offset 0, so a whole-document rewrite of this file is a readable diff.
NODES_YAML = """\
# nodes.yaml for the cluster
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
dcs-b-01:   # the second machine
  listen_address: 127.0.0.1
  listen_port: 10043
  probe_key: keep-me          # unrelated to instances
  DCS:
    installation: /opt/dcs
    allow_unsafe_api:
      - scripting
      - gui
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6667
      webgui_port: 8090
      dcs_port: 10318
"""

OTHER_NODE = "dcs-b-01"
TARGET_NODE = "dcs-a-01"


# -------------------------------------------------------------------------------------- doubles

class _Conn:
    def __init__(self, log):
        self._log = log

    async def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))


class _Ctx:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return _Conn(self._log)

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """``apool`` as the writers use it: a context manager handing back a recording connection."""

    def __init__(self):
        self.log: list[tuple] = []

    def connection(self):
        return _Ctx(self.log)


class _Factory:
    """``DataObjectFactory`` double: returns a pre-built instance for the one ``InstanceImpl`` request."""

    def __init__(self, instance):
        self._instance = instance

    def new(self, cls, **kwargs):
        return self._instance


# -------------------------------------------------------------------------------------- fixtures

@pytest.fixture
def install(tmp_path):
    """A config dir holding the two-node ``nodes.yaml`` and the ``servers.yaml`` the tooling reads."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text("{}\n", encoding="utf-8")
    return config_dir


def _block(text: str, node: str) -> str:
    """The raw text of one top-level node block — its ``node:`` line (trailing comment included) and body."""
    found = re.search(rf"(?m)^{re.escape(node)}:[^\n]*\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node} in:\n{text}"
    return found.group(0)


def _instance(node, name: str, *, dcs_port=10308, webgui_port=8088) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name, node=node,
        dcs_port=dcs_port, webgui_port=webgui_port,
        bot_port=6666, locale={}, options={})


def _node(monkeypatch, config_dir: Path, *, master=True) -> NodeImpl:
    """A real ``NodeImpl`` in its local shape; the instance-config collaborators are doubles."""
    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(config_dir)
    node._master = master
    node.locals = {}
    node.log = logging.getLogger("tests.node_config_write")
    node.apool = _Pool()
    node.instances = {}
    node.all_nodes = {TARGET_NODE: node}
    # ``set_config`` returns ``read_locals()``; the write is what is under test, not the read-back.
    node.read_locals = lambda: {"name": node.name}
    return node


def _read(config_dir: Path) -> str:
    return (config_dir / "nodes.yaml").read_text(encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. set_config — only the target node's block changes

@pytest.mark.asyncio
async def test_set_config_leaves_every_other_node_block_byte_identical(install, monkeypatch):
    before = _read(install)
    node = _node(monkeypatch, install)

    await node.set_config({"listen_port": 10099, "use_upnp": False})

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "# nodes.yaml for the cluster" in after
    assert "probe_key: keep-me          # unrelated to instances" in after
    target = _block(after, TARGET_NODE)
    assert "listen_port: 10099" in target and "use_upnp: false" in target, \
        "the merged keys must really be written"
    assert target != _block(before, TARGET_NODE), "the target block must have changed"
    assert (install / "nodes.yaml.bak").read_text(encoding="utf-8") == before, \
        "the previous generation is kept beside the file, as for every other config write"


@pytest.mark.asyncio
async def test_set_config_preserves_keys_it_does_not_merge(install, monkeypatch):
    before = _read(install)
    node = _node(monkeypatch, install)

    await node.set_config({"listen_port": 10099})

    target = _block(_read(install), TARGET_NODE)
    assert "listen_address: 127.0.0.1" in target, "an untouched key must survive the deep merge"
    assert "Syria" in target, "the node's instances must survive the deep merge"
    assert _block(_read(install), OTHER_NODE) == _block(before, OTHER_NODE)


@pytest.mark.asyncio
async def test_set_config_refuses_a_file_without_this_nodes_block(install, monkeypatch):
    configwrite.patch_config_file(install / "nodes.yaml",
                                  lambda document: document.pop(TARGET_NODE), schemas=None)
    before = _read(install)
    node = _node(monkeypatch, install)

    with pytest.raises(ConfigWriteError) as caught:
        await node.set_config({"listen_port": 10099})

    assert TARGET_NODE in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. init_instances — the dirty write stamps ports into the target block only

def _installs_double(monkeypatch, instance):
    monkeypatch.setattr(nodeimpl, "DataObjectFactory", lambda: _Factory(instance))
    monkeypatch.setattr(nodeimpl.utils, "findDCSInstances", lambda: {})


@pytest.mark.asyncio
async def test_init_instances_stamps_ports_and_leaves_other_block_byte_identical(install, monkeypatch):
    before = _read(install)
    node = _node(monkeypatch, install)
    node.locals = {"instances": {"Syria": {"home": "/home/a-syria", "bot_port": 6666}}}
    instance = _instance(node, "Syria")
    _installs_double(monkeypatch, instance)

    await node.init_instances()

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "probe_key: keep-me          # unrelated to instances" in after
    target = _block(after, TARGET_NODE)
    assert "dcs_port: 10308" in target and "webgui_port: 8088" in target, \
        "the missing ports must really be stamped"
    assert "home: /home/a-syria" in target and "bot_port: 6666" in target, \
        "existing keys must survive"
    assert node.apool.log and node.apool.log[0][1] == (TARGET_NODE,), \
        "the DELETE of this node's instances still runs"


@pytest.mark.asyncio
async def test_init_instances_writes_nothing_when_no_port_needs_stamping(install, monkeypatch):
    """The old ``dirty`` gate, now the primitive's no-op rule: a clean file is untouched."""
    configwrite.patch_config_file(
        install / "nodes.yaml",
        lambda document: document[TARGET_NODE]["instances"]["Syria"].update(
            {"dcs_port": 10308, "webgui_port": 8088}),
        schemas=None)
    (install / "nodes.yaml.bak").unlink()  # the setup's own write left one; the run must not
    before = _read(install)
    node = _node(monkeypatch, install)
    node.locals = {"instances": {"Syria": {"home": "/home/a-syria", "bot_port": 6666}}}
    instance = _instance(node, "Syria")
    _installs_double(monkeypatch, instance)

    await node.init_instances()

    assert _read(install) == before, "a no-op mutate must not rewrite the file"
    assert not (install / "nodes.yaml.bak").exists(), "and must not leave a .bak behind"


@pytest.mark.asyncio
async def test_init_instances_refuses_a_file_without_this_nodes_block(install, monkeypatch):
    configwrite.patch_config_file(install / "nodes.yaml",
                                  lambda document: document.pop(TARGET_NODE), schemas=None)
    before = _read(install)
    node = _node(monkeypatch, install)
    node.locals = {"instances": {"Syria": {"home": "/home/a-syria", "bot_port": 6666}}}
    instance = _instance(node, "Syria")
    _installs_double(monkeypatch, instance)

    with pytest.raises(ConfigWriteError) as caught:
        await node.init_instances()

    assert TARGET_NODE in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"
