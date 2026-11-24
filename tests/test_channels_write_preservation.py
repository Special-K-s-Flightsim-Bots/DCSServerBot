"""The channels write must preserve keys it does not name, and the master's view of an agent-hosted
server must follow the write.

Proved against the real ``ServerImpl.update_channels`` (the owning node's write) and
``ServerProxy.update_channels`` (the master's view): a helper that replaced the whole ``channels``
map dropped a key it did not name, and a proxy that forwarded the RPC without moving its own
``locals``/channel cache showed stale rows until a restart."""
from __future__ import annotations

import logging
import types
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from core.data.const import Channel
from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy

pytestmark = pytest.mark.asyncio

SERVER = "SRS-1"


# ---------------------------------------------------------------------------------- helpers

def node_for(tmp_path, *, name="agent-node", slow_system=False) -> types.SimpleNamespace:
    """The slice of a Node ``update_channels`` reads: ``config_dir`` (and ``slow_system``/``name``)."""
    return types.SimpleNamespace(config_dir=str(tmp_path), name=name, slow_system=slow_system)


def write_servers_yaml(tmp_path, mapping) -> Path:
    path = Path(tmp_path) / "servers.yaml"
    with path.open("w", encoding="utf-8") as f:
        YAML().dump(mapping, f)
    return path


def read_servers_yaml(tmp_path) -> dict:
    return YAML().load((Path(tmp_path) / "servers.yaml").read_text(encoding="utf-8"))


def impl(tmp_path, *, name=SERVER, locals_) -> ServerImpl:
    """The REAL ``ServerImpl.update_channels``, wired with only the attributes it touches.

    ``object.__new__`` skips ``__post_init__`` (which registers a DB row and a watchdog observer) so
    the test exercises the METHOD itself: the file read/merge/write and the ``locals``/cache update.
    """
    server = object.__new__(ServerImpl)
    server.name = name
    server.node = node_for(tmp_path)
    server.locals = locals_
    server._channels = {}
    server.log = logging.getLogger("test.serverimpl")
    return server


class AgentBus:
    """The RPC hop to an AGENT node: the AGENT applies the write to its OWN ``servers.yaml``/locals,
    then the call returns. The master's ``ServerProxy`` is the object under test — its view must then
    follow, and only it can be blamed when it does not."""

    def __init__(self, agent_dir):
        self.agent_dir = agent_dir
        self.calls: list[tuple] = []
        self.agent_channels: dict = {}

    async def send_to_node_sync(self, message, *, node=None, timeout=None):
        self.calls.append((message, node, timeout))
        assert message["method"] == "update_channels"
        channels = message["params"]["channels"]
        data = read_servers_yaml(self.agent_dir)
        current = dict(data[SERVER].get("channels") or {})
        current.update(channels)
        data[SERVER]["channels"] = current
        with (Path(self.agent_dir) / "servers.yaml").open("w", encoding="utf-8") as f:
            YAML().dump(data, f)
        self.agent_channels = current
        return None


def proxy(agent_dir, *, locals_) -> ServerProxy:
    """The MASTER's ``ServerProxy`` for an agent-hosted server — the object whose view must follow."""
    server = object.__new__(ServerProxy)
    server.name = SERVER
    server.node = node_for(agent_dir, name="master", slow_system=False)
    server.bus = AgentBus(agent_dir)
    server.locals = locals_
    server._channels = {}
    server.log = logging.getLogger("test.serverproxy")
    return server


# ---------------------------------------- 1. the owning node preserves keys it does not name

async def test_the_owning_node_preserves_keys_it_does_not_name(tmp_path):
    """A key the write does not name (``events``/``voice``) must survive: the merge base is the FILE's
    current map, not the caller's partial map."""
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "events": 900, "voice": 901}}})
    server = impl(tmp_path, locals_={"channels": {"status": 10, "events": 900, "voice": 901}})

    await server.update_channels({"status": 12})

    data = read_servers_yaml(tmp_path)
    assert data[SERVER]["channels"]["status"] == 12
    assert data[SERVER]["channels"]["events"] == 900
    assert data[SERVER]["channels"]["voice"] == 901


async def test_a_discord_callers_partial_map_does_not_wipe_unedited_keys(tmp_path):
    """The Discord editor calls ``update_channels`` with only the trio it shows (``status``/``chat``/
    ``admin``); the keys it never mentions keep their stored value."""
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"events": 900, "voice": 901, "audit": 902}}})
 # the Discord caller's map, as plugins/scheduler/commands.py builds it
    channels = {"status": -1, "chat": -1}
    server = impl(tmp_path, locals_={"channels": {"events": 900, "voice": 901, "audit": 902}})

    await server.update_channels(channels)

    data = read_servers_yaml(tmp_path)["SRS-1"]["channels"]
    assert data["status"] == -1 and data["chat"] == -1, "the caller's own keys land"
    assert data["events"] == 900 and data["voice"] == 901 and data["audit"] == 902, \
        "a key the Discord caller never named is preserved"


async def test_a_hand_edit_after_boot_survives_a_write(tmp_path):
    """Preservation must hold even after a key is edited BY HAND after boot (``voice`` 901 -> 999 in the
    file): the file's own 999 wins over the stale caller map."""
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "voice": 999}}})  # the hand-edit
    server = impl(tmp_path, locals_={"channels": {"status": 10, "voice": 901}})   # STALE locals

    await server.update_channels({"status": 12})

    data = read_servers_yaml(tmp_path)["SRS-1"]["channels"]
    assert data["status"] == 12
    assert data["voice"] == 999, "a hand-edit the caller never named is not reverted"


# ------------------------------------ 2. the master's view of an agent-hosted server follows

async def test_the_master_view_of_an_agent_hosted_server_follows_the_write(tmp_path):
    """``ServerProxy.update_channels`` must also move the MASTER's own ``locals``/channel cache after the
    agent RPC succeeds, or a re-render shows the old channel until a restart."""
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "voice": 901}}})
    server = proxy(tmp_path, locals_={"channels": {"status": 10, "voice": 901}})
    assert server.channels[Channel.STATUS] == 10, "precondition: the master starts on the old channel"

    await server.update_channels({"status": 12})

 # the agent really applied it (proof the hop happened/landed)...
    assert server.bus.agent_channels["status"] == 12
    assert read_servers_yaml(tmp_path)["SRS-1"]["channels"]["status"] == 12
 #... and the MASTER's own view followed, with no restart
    assert server.locals["channels"]["status"] == 12
    assert server.channels[Channel.STATUS] == 12, "the master's channel cache must reflect the write"
    assert server.channels[Channel.VOICE] == 901, "an unnamed key is untouched in the master's view too"


async def test_the_master_view_clears_a_stale_channel_cache(tmp_path):
    """The cache is the second half of the blocker: a ``_channels`` populated BEFORE the write must be
    invalidated, or a consumer reading it keeps the old id. FAIL-BEFORE: the warmed cache survived."""
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = proxy(tmp_path, locals_={"channels": {"status": 10}})
    warmed = server.channels               # populate the cache with the OLD value
    assert warmed[Channel.STATUS] == 10

    await server.update_channels({"status": 12})

    assert server.channels is not warmed, "the stale cache must be cleared, not reused"
    assert server.channels[Channel.STATUS] == 12
