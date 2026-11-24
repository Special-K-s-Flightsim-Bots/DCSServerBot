"""``delete_instance`` also removes the server's key from ``servers.yaml`` — the name stays reusable.

``servers.yaml`` is keyed by server name, and that key is what a re-add collides with
(``ServerImpl._servers_yaml_keys`` feeds ``_rename_refusal``), so a delete that leaves the key behind
makes the name unusable until the file is edited by hand. Both surfaces — Discord's
``/node delete_instance`` and the website's Delete server — reach the shared delete path
(``NodeImpl.delete_instance``), which is where the removal lives.

The file is CLUSTER-WIDE: it holds every server's settings and the operator's comments, so the write
is key-scoped through ``configwrite.patch_config_file`` — a whole-document re-dump is a diff nobody
asked for.

Pinned here:

* the target key is gone, the OTHER server's block is BYTE-identical and the comments survive;
* the acceptance that matters: the deleted name passes the collision guard again (the file looking
  right is not the criterion) — with a positive control that it is refused BEFORE the delete;
* an instance that carries no server, and the reserved blank ``n/a`` marker, never remove a key;
* a missing key and a missing file are not errors — the delete still completes;
* why no schema is passed, with the measurement behind it.
"""
from __future__ import annotations

import logging
import re
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.impl.serverimpl import ServerImpl
from core.utils import configwrite

REPO_ROOT = Path(__file__).resolve().parents[1]

#: TWO node blocks for ``nodes.yaml`` — only the target's own block is written by the delete.
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
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6667
      webgui_port: 8090
      dcs_port: 10318
"""

#: TWO server blocks in the primitive's canonical style (mapping=2, sequence=4, offset=2), with the
#: header comment, a trailing comment on the target's own key line and a comment inside the OTHER
#: server's block: the three shapes a whole-document re-dump disturbs.
SERVERS_YAML = """\
# servers.yaml for the cluster
DEFAULT:    # the shared defaults, inherited by every server
  message_timeout: 10
Syria:      # the server to delete
  message_timeout: 10
Nevada:
  message_timeout: 12   # the other server keeps its settings
  messages:
    message_ban: 'You are banned.'
"""

OTHER_SERVER = "Nevada"
TARGET_SERVER = "Syria"
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
    """``apool`` as ``delete_instance`` uses it: a context manager handing back a recording connection."""

    def __init__(self):
        self.log: list[tuple] = []

    def connection(self):
        return _Ctx(self.log)


class _Bus:
    def __init__(self):
        self.servers: dict = {}

    async def send_init(self, server):
        pass


class _Registry:
    """``ServiceRegistry`` double — the bus ``unregister_server`` pops the entry from."""

    bus = _Bus()

    @classmethod
    def get(cls, t):
        return cls.bus


# -------------------------------------------------------------------------------------- fixtures

@pytest.fixture
def install(tmp_path, monkeypatch):
    """A config dir holding ``nodes.yaml`` and ``servers.yaml``, plus the binding the validator reads.

    ``core.commandline.COMMAND_LINE_ARGS`` is bound to it because pykwalify loads the bot's
    ``core/utils/validators.py`` as a FRESH module for every validation and that module resolves
    ``NodeData`` (nodes.yaml + servers.yaml) through this binding.
    """
    config_dir = tmp_path / "config"
    (config_dir / "plugins").mkdir(parents=True)
    (config_dir / "services").mkdir()
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")

    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(config=str(config_dir), node="pytest"),
                        raising=False)
    return config_dir


def _servers(config_dir: Path) -> str:
    return (config_dir / "servers.yaml").read_text(encoding="utf-8")


def _block(text: str, server: str) -> str:
    """The raw text of one top-level server block — its ``server:`` line (trailing comment included)."""
    found = re.search(rf"(?m)^{re.escape(server)}:[^\n]*\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {server} in:\n{text}"
    return found.group(0)


def _instance(config_dir: Path, name: str, server_name: str | None = TARGET_SERVER):
    home = config_dir.parent / name
    home.mkdir(exist_ok=True)
    instance = types.SimpleNamespace(
        name=name, home=str(home), missions_dir=str(home / "Missions"),
        bot_port=6666, dcs_port=10308, webgui_port=8088,
        locals={"bot_port": 6666, "dcs_port": 10308, "webgui_port": 8088},
        node=types.SimpleNamespace(name=TARGET_NODE), server=None)
    if server_name is not None:
        instance.server = types.SimpleNamespace(name=server_name, instance=instance)
    return instance


def _node(monkeypatch, config_dir: Path, instance, *, instances=None) -> NodeImpl:
    """A real ``NodeImpl`` in its local shape; the instance-config collaborators are doubles."""
    monkeypatch.setattr(nodeimpl, "ServiceRegistry", _Registry)

    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(config_dir)
    node._master = True
    node.locals = {}
    node.node = types.SimpleNamespace(name=TARGET_NODE, master=True)
    node.log = logging.getLogger("tests.node_delete_servers_key")
    node.apool = _Pool()
    node.instances = dict(instances or {})
    node.all_nodes = {TARGET_NODE: node}
    _Registry.bus.servers = {instance.server.name: instance.server} if instance.server else {}
    return node


def _guard(config_dir: Path, name: str, *, bus_servers=None) -> str | None:
    """``ServerImpl``'s own collision refusal for *name*, against the file on disk.

    ``_rename_refusal`` is the guard a re-add goes through (``Server.rename`` →
    ``RenameError``), and it is what refuses a name whose key is still in ``servers.yaml``. Run
    unbound on a double: it reads ``self.name``, ``self.node.config_dir`` and ``self.bus`` only.
    """
    server = types.SimpleNamespace(
        name="", node=types.SimpleNamespace(config_dir=str(config_dir)),
        bus=types.SimpleNamespace(servers=dict(bus_servers or {})))
    server._servers_yaml_keys = types.MethodType(ServerImpl._servers_yaml_keys, server)
    return ServerImpl._rename_refusal(server, name)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. the key is removed, key-scoped — the other server's block keeps its bytes and the comments live

@pytest.mark.asyncio
async def test_delete_instance_removes_the_servers_yaml_key(install, monkeypatch):
    before = _servers(install)
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    after = _servers(install)
    assert not re.search(rf"(?m)^{re.escape(TARGET_SERVER)}:", after), \
        "the deleted server's key line must not survive"
    assert _block(after, OTHER_SERVER) == _block(before, OTHER_SERVER), \
        "the other server's block must be byte-identical"
    assert _block(after, "DEFAULT") == _block(before, "DEFAULT"), \
        "the shared DEFAULT block must be byte-identical"
    assert "# servers.yaml for the cluster" in after, "the header comment must survive"
    assert "# the other server keeps its settings" in after, "a comment inside another block must survive"
    assert "message_timeout: 12" in after, "the other server's settings must survive"
    assert (install / "servers.yaml.bak").read_text(encoding="utf-8") == before, \
        "the previous generation is kept beside the file, as for every other config write"


@pytest.mark.asyncio
async def test_the_deleted_name_can_be_used_again(install, monkeypatch):
    """The acceptance: the collision guard that refused the re-add no longer finds the name."""
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    # positive control: BEFORE the delete the guard refuses the name — the file's key is what refuses it
    assert _guard(install, TARGET_SERVER) is not None, \
        "the leftover key must be what refuses the re-add before the delete"

    await node.delete_instance(instance, remove_files=False)

    assert _guard(install, TARGET_SERVER) is None, \
        "after the delete the name must pass the collision guard again"
    keys = [str(key) for key in configwrite.load_config_file(install / "servers.yaml").keys()]
    assert TARGET_SERVER not in keys and OTHER_SERVER in keys, \
        f"servers.yaml must hold exactly the surviving servers, got {keys}"


@pytest.mark.asyncio
async def test_an_instance_without_a_server_removes_no_key(install, monkeypatch):
    """No server bound — there is no name, so servers.yaml is not written at all."""
    before = _servers(install)
    instance = _instance(install, "Syria", server_name=None)
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    assert _servers(install) == before, "an instance with no server has no key to remove"
    assert not (install / "servers.yaml.bak").exists(), "nothing was written, so no .bak either"


@pytest.mark.asyncio
async def test_the_blank_marker_is_never_written_as_a_key(install, monkeypatch):
    """``n/a`` is the code's \"unnamed\" marker — it must never be looked up or written as a key."""
    before = _servers(install)
    instance = _instance(install, "Syria", server_name="n/a")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    assert _servers(install) == before, "a blank 'n/a' server must not touch servers.yaml"
    keys = [str(key) for key in configwrite.load_config_file(install / "servers.yaml").keys()]
    assert "n/a" not in keys


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. a missing key and a missing file are not errors — the delete still completes

@pytest.mark.asyncio
async def test_a_missing_key_does_not_stop_the_delete(install, monkeypatch):
    configwrite.patch_config_file(install / "servers.yaml",
                                  lambda document: document.pop(TARGET_SERVER), schemas=None)
    before = _servers(install)
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    assert _servers(install) == before, "a key the file does not carry is a no-op, not an error"
    assert "Syria" not in (install / "nodes.yaml").read_text(encoding="utf-8"), \
        "the delete must still have removed the instance from nodes.yaml"
    assert [row[0] for row in node.apool.log] == ["DELETE FROM instances WHERE instance = %s"], \
        "the delete must still have reached the database"


@pytest.mark.asyncio
async def test_a_missing_servers_yaml_is_not_an_error(install, monkeypatch):
    (install / "servers.yaml").unlink()
    instance = _instance(install, "Syria")
    node = _node(monkeypatch, install, instance, instances={"Syria": instance})

    await node.delete_instance(instance, remove_files=False)

    assert not (install / "servers.yaml").exists(), \
        "a delete must not CREATE a servers.yaml on a node that has none"
    assert "Syria" not in (install / "nodes.yaml").read_text(encoding="utf-8"), \
        "the delete must still have removed the instance from nodes.yaml"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. why the removal passes no schema — the measurement behind ``schemas=None``

def test_the_servers_schema_is_whole_document_so_no_schema_is_passed(install):
    """``schemas=None`` is a decision, not an omission — measured against the shipped schema.

    ``schemas/servers_schema.yaml`` validates the WHOLE document (one regex-keyed block per server), so
    a fault in ANOTHER server's block would refuse this edit. Popping a key keeps a valid document
    valid — including popping the last one — so the removal itself is not the reason; the blast radius
    of a whole-document check is.
    """
    path = install / "servers.yaml"
    schemas = [str(REPO_ROOT / "schemas" / "servers_schema.yaml")]

    # anti-vacuity: the fixture IS valid, so the refusal below is the edit's and not the fixture's
    configwrite.validate_against_schema(SERVERS_YAML, schemas, path=path)

    popped = configwrite.load_config_text(SERVERS_YAML)
    popped.pop(TARGET_SERVER)
    configwrite.validate_against_schema(configwrite.dump_config(popped), schemas, path=path)

    emptied = configwrite.load_config_text(SERVERS_YAML)
    emptied.pop(TARGET_SERVER)
    emptied.pop(OTHER_SERVER)
    emptied.pop("DEFAULT")
    configwrite.validate_against_schema(configwrite.dump_config(emptied), schemas, path=path)

    # a fault in the OTHER server's block refuses the edit — the reason no schema is passed
    faulty = configwrite.load_config_text(SERVERS_YAML)
    faulty[OTHER_SERVER]["message_timeout"] = 999
    faulty.pop(TARGET_SERVER)
    with pytest.raises(configwrite.ConfigValidationError) as caught:
        configwrite.validate_against_schema(configwrite.dump_config(faulty), schemas, path=path)
    assert "/Nevada/message_timeout" in caught.value.errors
