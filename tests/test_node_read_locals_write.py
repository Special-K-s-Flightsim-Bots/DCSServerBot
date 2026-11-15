"""``read_locals``' dirty write: the stamps go into THIS node's block, through the write primitive.

``config/nodes.yaml`` is cluster-wide — one top-level block per node. ``NodeImpl.read_locals`` still
cleaned up the operator's file itself: it removed a plaintext database/DCS password and stamped
``use_upnp``, then dumped the WHOLE document back with a bare emitter. Every other node's block came
back re-serialised (a hand-maintained sequence re-indented, a long scalar re-folded) and the file was
read long before it was written, so a concurrent writer's change in between was silently lost.

It now writes through ``configwrite.patch_config_file`` with a mutate scoped to this node's block:

* **only this node's block changes** — every other top-level node key is BYTE-identical after the
  call, its trailing comment, its standalone comment, its LONG plain scalar and its SEQUENCE
  included. The fixture is in the canonical style the primitive serialises, so nothing but the
  intended stamp may differ;
* **each stamp really landed** — ``use_upnp``, the SECRET database URL and the ``user`` that replaces
  a removed DCS password are read back out of the target block;
* **a file without this node's block is refused loudly** — ``ConfigWriteError`` with the file left
  byte-identical, instead of a bare ``KeyError`` from the bare emitter's ``data.get(...)`` path
  (the pre-write ``InstallationException`` still fires first when the block is absent altogether).

Validation is a double here: ``read_locals`` consults ``utils.validate`` before it reads, and that
path needs the bot's ``schemas/`` tree relative to the process CWD. The write is what is under test,
so the test records that validation was consulted and returns no errors.
"""
from __future__ import annotations

import logging
import re
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.node import InstallationException
from core.utils import configwrite

#: TWO node blocks in the primitive's canonical style (mapping=2, sequence=4, offset=2). The second
#: one carries the three shapes a whole-document re-dump disturbs: a trailing comment on its ``key:``
#: line, a standalone comment line, a LONG plain scalar a default-width emitter would re-fold, and a
#: SEQUENCE a bare emitter re-indents. The first block has no ``use_upnp``, so a plain read stamps it.
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
  # this block must survive a write to the other one, byte for byte
  notes: this deliberately long plain scalar is exactly what a default-width emitter would re-fold onto two or more lines, which is the reformatting a whole-document rewrite produces
  allow_unsafe_api:
    - scripting
    - gui
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6667
"""

#: The same two blocks, with the plaintext secrets ``read_locals`` is meant to strip from the target.
NODES_YAML_SECRETS = """\
# nodes.yaml for the cluster
dcs-a-01:
  listen_address: 127.0.0.1
  database:
    url: postgresql://dcssb:SuperSecret@127.0.0.1:5432/dcssb
  DCS:
    installation: /opt/dcs
    dcs_user: operator
    dcs_password: PlainTextPassword
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
dcs-b-01:   # the second machine
  listen_address: 127.0.0.1
  # this block must survive a write to the other one, byte for byte
  notes: this deliberately long plain scalar is exactly what a default-width emitter would re-fold onto two or more lines, which is the reformatting a whole-document rewrite produces
  allow_unsafe_api:
    - scripting
    - gui
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6667
"""

OTHER_NODE = "dcs-b-01"
TARGET_NODE = "dcs-a-01"


@pytest.fixture
def install(tmp_path):
    """A config dir holding the two-node ``nodes.yaml`` and the ``servers.yaml`` the tooling reads."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (config_dir / "servers.yaml").write_text("{}\n", encoding="utf-8")
    return config_dir


@pytest.fixture
def install_secrets(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "nodes.yaml").write_text(NODES_YAML_SECRETS, encoding="utf-8")
    (config_dir / "servers.yaml").write_text("{}\n", encoding="utf-8")
    return config_dir


def _block(text: str, node: str) -> str:
    """The raw text of one top-level node block — its ``node:`` line (trailing comment included) and body."""
    found = re.search(rf"(?m)^{re.escape(node)}:[^\n]*\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node} in:\n{text}"
    return found.group(0)


def _read(config_dir: Path) -> str:
    return (config_dir / "nodes.yaml").read_text(encoding="utf-8")


def _node(monkeypatch, config_dir: Path, *, validation: str = "lazy",
          upnp: bool = False) -> tuple[NodeImpl, dict]:
    """A real ``NodeImpl`` read path; validation and UPnP probing are doubles, recorded when consulted."""
    consulted: list = []
    monkeypatch.setattr(nodeimpl.utils, "validate",
                        lambda *a, **kw: consulted.append(("validate", a[0], tuple(a[1]))))
    monkeypatch.setattr(nodeimpl.utils, "is_upnp_available", lambda: upnp)

    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(config_dir)
    node.log = logging.getLogger("tests.node_read_locals")
    node.all_nodes = {}
    node.node = types.SimpleNamespace(name=TARGET_NODE, config={"validation": validation})
    return node, {"consulted": consulted}


def _secrets_recorder(monkeypatch) -> list:
    written: list = []
    monkeypatch.setattr(nodeimpl.utils, "set_password",
                        lambda what, password, config_dir: written.append((what, password)))
    return written


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. the use_upnp stamp: only this node's block changes

def test_read_locals_stamps_use_upnp_and_leaves_the_other_node_block_byte_identical(install, monkeypatch):
    before = _read(install)
    node, state = _node(monkeypatch, install)

    node.read_locals()

    after = _read(install)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    assert "# nodes.yaml for the cluster" in after
    assert "# this block must survive a write to the other one, byte for byte" in after
    assert "  notes: this deliberately long plain scalar is exactly what a default-width emitter " \
           "would re-fold onto two or more lines, which is the reformatting a whole-document " \
           "rewrite produces\n" in after, "the long plain scalar must not be re-folded"
    assert "    - scripting\n    - gui\n" in after, "the sequence must keep its own indentation"
    target = _block(after, TARGET_NODE)
    assert "use_upnp: false" in target, "the stamp must really be written"
    assert target != _block(before, TARGET_NODE), "the target block must have changed"
    assert (install / "nodes.yaml.bak").read_text(encoding="utf-8") == before, \
        "the previous generation is kept beside the file, as for every other config write"
    assert state["consulted"], "read_locals still consults the validator before it reads"


def test_read_locals_keeps_the_stamp_it_read_when_the_file_already_carries_it(install, monkeypatch):
    """The returned locals still carry the node's own block, ``use_upnp`` included, and no second write."""
    node, _ = _node(monkeypatch, install)
    locals1 = node.read_locals()
    assert locals1["use_upnp"] is False
    before = _read(install)

    locals2 = node.read_locals()

    assert locals2["use_upnp"] is False, "the stamped value is what the caller reads back"
    assert _read(install) == before, "a file that already carries use_upnp is not rewritten"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. the two secret removals: also scoped to this node's block

def test_read_locals_removes_a_plaintext_database_url_from_this_block_only(install_secrets, monkeypatch):
    before = _read(install_secrets)
    node, _ = _node(monkeypatch, install_secrets)
    written = _secrets_recorder(monkeypatch)

    locals_ = node.read_locals()

    assert ("database", "SuperSecret") in written, "the password is handed to the secret store"
    after = _read(install_secrets)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    target = _block(after, TARGET_NODE)
    assert "dcssb:SECRET@" in target and "SuperSecret" not in target, \
        "the plaintext password must be gone from the file"
    assert locals_["database"]["url"].startswith("postgresql://dcssb:SECRET@") \
        and "SuperSecret" not in locals_["database"]["url"], \
        "the returned locals carry the rebuilt SECRET URL"


def test_read_locals_removes_a_plaintext_dcs_password_from_this_block_only(install_secrets, monkeypatch):
    before = _read(install_secrets)
    node, _ = _node(monkeypatch, install_secrets)
    written = _secrets_recorder(monkeypatch)

    locals_ = node.read_locals()

    assert ("DCS", "PlainTextPassword") in written, "the DCS password is handed to the secret store"
    after = _read(install_secrets)
    assert _block(after, OTHER_NODE) == _block(before, OTHER_NODE), \
        "the other node's block must not be re-serialised"
    target = _block(after, TARGET_NODE)
    assert "dcs_password" not in target and "PlainTextPassword" not in target, \
        "the plaintext password must be gone from the file"
    assert "user: operator" in target, "dcs_user becomes the user key"
    assert locals_["DCS"]["user"] == "operator", "the returned locals carry the renamed key"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. a missing block is refused before anything is written

def test_read_locals_refuses_a_file_without_this_nodes_block(install, monkeypatch):
    path = install / "nodes.yaml"
    configwrite.patch_config_file(path, lambda document: document.pop(TARGET_NODE), schemas=None)
    before = _read(install)
    node, _ = _node(monkeypatch, install)

    with pytest.raises(InstallationException) as caught:
        node.read_locals()

    assert TARGET_NODE in str(caught.value)
    assert _read(install) == before, "a refusal writes nothing"
