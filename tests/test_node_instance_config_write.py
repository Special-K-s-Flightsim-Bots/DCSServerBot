"""``nodes.yaml`` instance writes: the ONE config-write primitive, scoped to this node's own block.

``config/nodes.yaml`` is cluster-wide — one top-level block per node, each holding its own
``instances``. ``add_instance``, ``delete_instance`` and ``rename_instance`` touch only their OWN
node's block, so a write that dumps the whole document re-serialises every other node's block as
well (a hand-maintained file comes back reformatted) and reads the file long before it writes it (a
concurrent writer's change in between is silently lost).

These tests pin the contract the three writers now have, through ``configwrite.patch_config_file``:

* **only the target node's block changes** — every other top-level node key is BYTE-identical after
  an add, a delete and a rename, its comments and its unrelated keys included. The fixture is in the
  canonical style the primitive serialises, so nothing but the intended change may differ;
* **the change really landed** — the target block carries the added/moved instance, so a test cannot
  pass by writing nothing;
* **a key the writer assumes and cannot find is refused loudly** — ``ConfigWriteError``, with the
  file left byte-identical, instead of a silently skipped delete or a bare ``KeyError``;
* **why no schema is passed** — the node schema's ``is_node`` rule resolves the candidate's instance
  names against the ON-DISK ``nodes.yaml``, so a schema-checked write would refuse exactly the new
  name an add/rename produces. Pinned so a later "let's validate this too" is a failing test.
"""
from __future__ import annotations

import logging
import re
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.instanceimpl import InstanceImpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.impl.serverimpl import ServerImpl
from core.utils import configwrite
from core.utils.configwrite import ConfigWriteError

REPO_ROOT = Path(__file__).resolve().parents[1]

#: TWO node blocks in the primitive's canonical style (mapping=2, sequence=4, offset=2) — verified to
#: re-serialise byte-for-byte, which is what makes "the other block is byte-identical" an assertion.
#: The second block carries a trailing comment on its own line AND an unrelated key (``probe_key``)
#: with its own comment, plus a SEQUENCE: the three shapes a whole-document re-dump disturbs — a bare
#: ``YAML()`` re-indents the sequence's dashes, so a re-dump of this block is a readable diff.
NODES_YAML = """\
# nodes.yaml for the cluster
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8088
      dcs_port: 10308
dcs-b-01:   # the second machine
  listen_address: 127.0.0.1
  listen_port: 10043
  probe_key: keep-me          # unrelated to instances
  DCS:
    installation: '%ProgramFiles%\\Eagle Dynamics\\DCS World'
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


class _Bus:
    def __init__(self):
        self.servers: dict = {}
        self.sent: list = []

    async def send_init(self, server):
        self.sent.append(server)


class _Registry:
    """``ServiceRegistry`` double: no services to stop/start, no singletons to fetch."""

    bus = _Bus()

    @classmethod
    def get(cls, t):
        return cls.bus

    @classmethod
    def services(cls) -> dict:
        return {}


class _Factory:
    """``DataObjectFactory`` double: the pre-built instance and server objects, nothing constructed."""

    def __init__(self, instance, server):
        self._instance = instance
        self._server = server

    def new(self, cls, **kwargs):
        return self._instance if cls is InstanceImpl else self._server


class _Autoexec:
    def __init__(self, instance=None):
        self.instance = instance
        self.crash_report_mode = None
        self.net = None


# -------------------------------------------------------------------------------------- fixtures

@pytest.fixture
def install(tmp_path, monkeypatch):
    """A config dir holding ``nodes.yaml`` (and the ``servers.yaml`` the validator's own data reads).

    ``core.commandline.COMMAND_LINE_ARGS`` is bound to it because pykwalify loads the bot's
    ``core/utils/validators.py`` as a FRESH module for every validation and that module resolves
    ``NodeData`` (nodes.yaml + servers.yaml) through this binding.
    """
    config_dir = tmp_path / "config"
    (config_dir / "plugins").mkdir(parents=True)
    (config_dir / "services").mkdir()
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text("{}\n", encoding="utf-8")

    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(config=str(config_dir), node="pytest"),
                        raising=False)
    return config_dir


def _block(text: str, node: str) -> str:
    """The raw text of one top-level node block — its ``node:`` line (trailing comment included) and body."""
    found = re.search(rf"(?m)^{re.escape(node)}:[^\n]*\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node} in:\n{text}"
    return found.group(0)


def _instance(config_dir: Path, name: str) -> types.SimpleNamespace:
    home = config_dir.parent / name
    home.mkdir(exist_ok=True)
    (home / "Missions").mkdir(exist_ok=True)
    return types.SimpleNamespace(
        name=name, home=str(home), missions_dir=str(home / "Missions"),
        bot_port=6668, dcs_port=10328, webgui_port=8092,
        locals={"bot_port": 6668, "dcs_port": 10328, "webgui_port": 8092},
        node=types.SimpleNamespace(name=TARGET_NODE), server=None)


def _node(monkeypatch, config_dir: Path, instance: types.SimpleNamespace, *,
          instances=None, master=True) -> NodeImpl:
    """A real ``NodeImpl`` in its local shape; the instance-config collaborators are doubles."""
    server = types.SimpleNamespace(name="n/a", settings={})
    monkeypatch.setattr(nodeimpl, "DataObjectFactory", lambda: _Factory(instance, server))
    monkeypatch.setattr(nodeimpl, "ServiceRegistry", _Registry)
    monkeypatch.setattr(nodeimpl, "Autoexec", _Autoexec)
    monkeypatch.setattr(nodeimpl, "SAVED_GAMES", str(config_dir.parent / "SavedGames"))

    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(config_dir)
    node._master = master
    node.locals = {}
    node.node = types.SimpleNamespace(name=TARGET_NODE, master=master)
    node.log = logging.getLogger("tests.node_instance_config")
    node.apool = _Pool()
    node.instances = dict(instances or {})
    node.all_nodes = {TARGET_NODE: node}
    return node


def _read(config_dir: Path) -> str:
    return (config_dir / "nodes.yaml").read_text(encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. only the target node's block changes — every other block keeps its bytes, comments and keys

@pytest.mark.asyncio
async def test_add_instance_leaves_every_other_node_block_byte_identical(install, monkeypatch):
    before = _read(install)
    node = _node(monkeypatch, install, _instance(install, "Caucasus"))

    await node.add_instance("Caucasus")

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "# nodes.yaml for the cluster" in after
    assert "probe_key: keep-me          # unrelated to instances" in after
    target = _block(after, TARGET_NODE)
    assert "Caucasus" in target and "bot_port: 6668" in target, "the instance must really be written"
    assert target != _block(before, TARGET_NODE), "the target block must have changed"
    assert (install / "nodes.yaml.bak").read_text(encoding="utf-8") == before, \
        "the previous generation is kept beside the file, as for every other config write"


@pytest.mark.asyncio
async def test_delete_instance_leaves_every_other_node_block_byte_identical(install, monkeypatch):
    before = _read(install)
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "# nodes.yaml for the cluster" in after
    assert "probe_key: keep-me          # unrelated to instances" in after
    assert "Syria" not in _block(after, TARGET_NODE), "the instance must really be dropped"
    assert "Nevada" in _block(after, OTHER_NODE), "the other node keeps its instance"


@pytest.mark.asyncio
async def test_rename_instance_leaves_every_other_node_block_byte_identical(install, monkeypatch):
    before = _read(install)
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance}, master=False)

    await node.rename_instance(instance, "Caucasus")

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "# nodes.yaml for the cluster" in after
    assert "probe_key: keep-me          # unrelated to instances" in after
    target = _block(after, TARGET_NODE)
    assert "Caucasus:" in target and "Syria:" not in target, "the instance must really be renamed"
    assert instance.name == "Caucasus" and instance.locals["home"].endswith("Caucasus")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. a key the writer assumes and cannot find is refused loudly, with the file untouched

@pytest.mark.asyncio
async def test_add_instance_refuses_a_file_without_this_nodes_block(install, monkeypatch):
    path = install / "nodes.yaml"
    configwrite.patch_config_file(path, lambda document: document.pop(TARGET_NODE), schemas=None)
    before = _read(install)
    node = _node(monkeypatch, install, _instance(install, "Caucasus"))

    with pytest.raises(ConfigWriteError) as caught:
        await node.add_instance("Caucasus")

    assert TARGET_NODE in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"


@pytest.mark.asyncio
async def test_delete_instance_refuses_an_instance_the_file_does_not_carry(install, monkeypatch):
    before = _read(install)
    instance = _instance(install, "Nowhere")
    node = _node(monkeypatch, install, instance, instances={"Nowhere": instance})

    with pytest.raises(ConfigWriteError) as caught:
        await node.delete_instance(instance, remove_files=False)

    assert "Nowhere" in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"


@pytest.mark.asyncio
async def test_rename_instance_refuses_an_instance_the_file_does_not_carry(install, monkeypatch):
    before = _read(install)
    instance = _instance(install, "Nowhere")
    node = _node(monkeypatch, install, instance, instances={"Nowhere": instance}, master=False)

    with pytest.raises(ConfigWriteError) as caught:
        await node.rename_instance(instance, "Caucasus")

    assert "Nowhere" in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. why these three writers pass no schema

def test_the_node_schema_would_refuse_the_candidates_these_writers_produce(install):
    """``schemas=None`` is a decision, not an omission: the schema refuses what these writers write.

    The candidate text is built from the SAME fixture with ``probe_key`` removed, because the node
    schema refuses any key it does not declare — which is why the fixture's unrelated key is a
    byte-identity witness here and cannot ride along in a schema check.
    """
    path = install / "nodes.yaml"
    schemas = [str(REPO_ROOT / "schemas" / "nodes_schema.yaml"),
               *[str(p) for p in sorted((REPO_ROOT / "extensions").rglob("*_schema.yaml"))]]
    assert len(schemas) > 1, "the node schema plus the extensions tree is the set the bot validates"

    def as_document(text: str):
        return configwrite.load_config_text(text)

    clean = NODES_YAML.replace("  probe_key: keep-me          # unrelated to instances\n", "")
    # anti-vacuity: the file these writers start from IS valid, so a refusal below is the edit's
    configwrite.validate_against_schema(clean, schemas, path=path)

    # the candidate add_instance writes: a name the ON-DISK nodes.yaml does not know yet
    added = as_document(clean)
    added[TARGET_NODE]["instances"]["Caucasus"] = {"home": "/home/a-caucasus", "bot_port": 6668}
    with pytest.raises(configwrite.ConfigValidationError) as caught:
        configwrite.validate_against_schema(configwrite.dump_config(added), schemas, path=path)
    assert "is_node" in caught.value.errors

    # the candidate rename_instance writes: the new name is equally unknown to the on-disk file
    renamed = as_document(clean)
    renamed[TARGET_NODE]["instances"]["Caucasus"] = renamed[TARGET_NODE]["instances"].pop("Syria")
    with pytest.raises(configwrite.ConfigValidationError) as caught:
        configwrite.validate_against_schema(configwrite.dump_config(renamed), schemas, path=path)
    assert "is_node" in caught.value.errors

    # the candidate delete_instance writes when it drops a node's last instance: schema-CLEAN — the
    # emptied instances map serialises as an empty map, not as a null. The delete writer still passes
    # no schema (the schema validates the WHOLE file, so a fault in another node's block would refuse
    # this edit), but this candidate is not itself the reason.
    emptied = as_document(clean)
    emptied[OTHER_NODE]["instances"].pop("Nevada")
    configwrite.validate_against_schema(configwrite.dump_config(emptied), schemas, path=path)
