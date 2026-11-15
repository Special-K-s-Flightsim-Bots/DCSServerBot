"""The channels write (``set_server_channels``), the bot face of the Configuration tab.

The action writes ``servers.yaml``'s per-server ``channels`` map through ``Server.update_channels``.
Contract: only changed rows are applied; every key the console does not edit is preserved; "not set"
is the explicit ``-1``, never blank; a channel id not in the guild, or a server that cannot be
resolved, is refused; the capability is the DCS write's (Admin; a DCS Admin is refused); the write
is audited once, naming the KEYS and never a value. The action is driven directly (the ``sa``
fixture is reused from ``test_scheduler_server_config``)."""
from __future__ import annotations

import os
import types

import pytest
from ruamel.yaml import YAML

import core.actions as actions_module
from core import Status
from core.actions import call_action

from test_scheduler_server_config import (  # noqa: F401 - fixtures imported on purpose
    RecordingBot, ctx_for, node_for, read_servers_yaml, sa, write_servers_yaml,
)

yaml = YAML()

SERVER = "SRS-1"


# ---------------------------------------------------------------------------------- test doubles

class ChannelServer:
    """A server double whose ``update_channels`` writes ``servers.yaml`` exactly like ``ServerImpl``.

    It mirrors ``core/data/impl/serverimpl.py`` (rewrite the entry's ``channels`` map, update
    ``locals``) so a test proves the write LANDED in the file, not merely that a method was called.
    ``fail`` models a write that raises, for the typed-failure path.
    """

    def __init__(self, name=SERVER, *, status=Status.SHUTDOWN, locals_=None, node=None, fail=False):
        self.name = name
        self.display_name = name
        self.status = status
        self.locals = locals_ or {}
        self.node = node
        self.fail = fail
        self.update_channels_calls: list[dict] = []

    async def update_channels(self, channels: dict) -> None:
        self.update_channels_calls.append(dict(channels))
        if self.fail:
            raise RuntimeError("the disk is full")
        config_file = os.path.join(self.node.config_dir, "servers.yaml")
        with open(config_file, mode="r", encoding="utf-8") as infile:
            config = yaml.load(infile)
 # Mirrors the REAL ``ServerImpl.update_channels``: the merge base is the FILE's current map, so
 # a key the write does not name is preserved.
        entry = config[self.name]
        current = entry.get("channels")
        current = dict(current) if isinstance(current, dict) else {}
        current.update(channels)
        entry["channels"] = current
        with open(config_file, mode="w", encoding="utf-8") as outfile:
            yaml.dump(config, outfile)
        self.locals.setdefault("channels", {}).update(channels)


def bot_with(*channel_ids, central=None):
    """A bot double: one guild holding *channel_ids*, and an optional central admin channel.

    ``bot.locals`` is the shape the bot reads from ``bot.yaml`` — ``{'channels': {'admin': <id>}}``
    when a central admin channel is declared, empty otherwise.
    """
    guild = types.SimpleNamespace(
        channels=[types.SimpleNamespace(id=cid, name=f"chan{cid}") for cid in channel_ids])
    locals_ = {"channels": {"admin": central}} if central else {}
    return types.SimpleNamespace(guilds=[guild], locals=locals_)


def guild_bot(monkeypatch, sa, *channel_ids, central=None):
    """Point the action's bot resolution at a stub for the duration of one test."""
    bot = bot_with(*channel_ids, central=central)
    monkeypatch.setattr(sa, "_live_bot", lambda: bot)
    return bot


@pytest.fixture
def audit_bot(monkeypatch):
    """Make the seam's audit resolvable, so entries are RECORDED and can be asserted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


# ------------------------------------------------------------ 1. the write lands in servers.yaml

@pytest.mark.asyncio
async def test_a_changed_channel_lands_in_servers_yaml(sa, monkeypatch, audit_bot, tmp_path):
    """END TO END: a changed row is written through ``update_channels`` into ``servers.yaml``.

    The new channel id is in the entry, the untouched row is preserved, and ``update_channels`` is called
    ONCE with ONLY the changed rows (the helper merges them onto the file's current map).
    """
    guild_bot(monkeypatch, sa, 10, 11, 12)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "chat": 11}}})
    server = ChannelServer(locals_={"channels": {"status": 10, "chat": 11}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, SERVER, {"status": 12})

    assert result.ok is True
    assert result.applied == {"status": {"from": 10, "to": 12}}
    data = read_servers_yaml(tmp_path)
    assert data[SERVER]["channels"]["status"] == 12, "the change must land in servers.yaml"
    assert data[SERVER]["channels"]["chat"] == 11, "the untouched row must be preserved"
    assert server.update_channels_calls == [{"status": 12}], \
        "only the changed rows are shipped; the helper preserves the rest from the file"


@pytest.mark.asyncio
async def test_keys_the_console_does_not_edit_are_preserved(sa, monkeypatch, audit_bot, tmp_path):
    """A key this console does not edit survives the save.

    Only the changed rows are shipped and the helper merges them onto the file's current map, which keeps
    the keys the console never showed.
    """
    guild_bot(monkeypatch, sa, 10, 11, 12)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "events": 900, "voice": 901}}})
    server = ChannelServer(locals_={"channels": {"status": 10, "events": 900, "voice": 901}},
                           node=node_for(tmp_path))
    ctx = ctx_for([server])

    await sa.set_server_channels(ctx, SERVER, {"status": 12})

    assert server.update_channels_calls == [{"status": 12}], \
        "the call carries ONLY the changed row"
    data = read_servers_yaml(tmp_path)
    assert data[SERVER]["channels"]["events"] == 900 and data[SERVER]["channels"]["voice"] == 901, \
        "a key this console does not edit is preserved, never wiped"


@pytest.mark.asyncio
async def test_a_hand_edit_after_boot_survives_a_console_save(sa, monkeypatch, audit_bot, tmp_path):
    """Preservation must hold after a key the console never showed is edited BY HAND after boot (``voice``
    901 -> 999): only the changed row travels, so the file's own 999 wins over the stale ``locals``.
    """
    guild_bot(monkeypatch, sa, 10, 11, 12)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "voice": 999}}})
    server = ChannelServer(locals_={"channels": {"status": 10, "voice": 901}},   # STALE locals
                           node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, SERVER, {"status": 12})

    assert result.ok is True
    data = read_servers_yaml(tmp_path)
    assert data[SERVER]["channels"]["status"] == 12
    assert data[SERVER]["channels"]["voice"] == 999, \
        "a hand-edit the console never showed is not reverted by a console save"


@pytest.mark.asyncio
async def test_only_changed_rows_are_applied(sa, monkeypatch, audit_bot, tmp_path):
    """A submitted value equal to the current one is unchanged: nothing is written."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, SERVER, {"status": 10})

    assert result.applied == {}
    assert "unchanged" in result.skipped["status"]
    assert server.update_channels_calls == [], "an unchanged write never touches the file"


@pytest.mark.asyncio
async def test_not_set_writes_minus_one_not_a_blank(sa, monkeypatch, audit_bot, tmp_path):
    """``-1`` is the bot's own "not set" and an explicit change; a blank field is never written."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10, "chat": 11}}})
    server = ChannelServer(locals_={"channels": {"status": 10, "chat": 11}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, SERVER, {"chat": -1})

    assert result.applied == {"chat": {"from": 11, "to": -1}}
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["chat"] == -1


# --------------------------------------------------------------- 2. refusals (foreign / unknown)

@pytest.mark.asyncio
async def test_a_channel_id_from_another_guild_is_refused(sa, monkeypatch, audit_bot, tmp_path):
    """A channel id this guild does not hold is skipped with a reason; nothing is written."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, SERVER, {"status": 999})

    assert result.applied == {}
    assert "not a channel of this guild" in result.skipped["status"]
    assert server.update_channels_calls == [], "a refused channel never reaches the file"
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["status"] == 10


@pytest.mark.asyncio
async def test_a_non_numeric_channel_is_refused(sa, monkeypatch, audit_bot, tmp_path):
    guild_bot(monkeypatch, sa, 10)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"status": "#general"})

    assert result.applied == {}
    assert "expects a channel id" in result.skipped["status"]


@pytest.mark.asyncio
async def test_an_unknown_channel_key_is_refused(sa, monkeypatch, audit_bot, tmp_path):
    guild_bot(monkeypatch, sa, 10)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"events": 10})

    assert result.applied == {}
    assert "not an editable channel" in result.skipped["events"]


@pytest.mark.asyncio
async def test_a_server_that_cannot_be_resolved_is_refused(sa, monkeypatch, audit_bot):
    guild_bot(monkeypatch, sa, 10)
    server = ChannelServer(locals_={"channels": {"status": 10}})
    ctx = ctx_for([server])

    result = await sa.set_server_channels(ctx, "NOPE-9", {"status": 10})

    assert result.ok is False and result.refused == "Server 'NOPE-9' not found."


@pytest.mark.asyncio
async def test_with_no_guild_a_channel_id_cannot_be_validated(sa, monkeypatch, audit_bot, tmp_path):
    """Fail closed: no bot/guild means a channel id cannot be shown to belong to it → refused."""
    monkeypatch.setattr(sa, "_live_bot", lambda: None)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {}}})
    server = ChannelServer(locals_={}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"status": 10})

    assert result.applied == {}
    assert "not connected to a Discord guild" in result.skipped["status"]
    assert server.update_channels_calls == []


# ------------------------------------------------- 3. the central admin rule (both faces agree)

@pytest.mark.asyncio
async def test_the_admin_key_is_not_per_server_when_central_is_defined(sa, monkeypatch, audit_bot,
                                                                      tmp_path):
    """When ``bot.yaml`` defines a central admin channel the per-server ``admin`` is SKIPPED."""
    guild_bot(monkeypatch, sa, 10, 11, 12, central=777)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"admin": 12})

    assert result.applied == {}
    assert "not a per-server setting" in result.skipped["admin"]
    assert server.update_channels_calls == []


@pytest.mark.asyncio
async def test_the_admin_key_is_editable_when_no_central_is_defined(sa, monkeypatch, audit_bot,
                                                                  tmp_path):
    """The positive control: with no central admin channel the per-server one is written normally."""
    guild_bot(monkeypatch, sa, 10, 12)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"admin": 12})

    assert result.applied == {"admin": {"from": -1, "to": 12}}
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["admin"] == 12


# ----------------------------------------------------------------- 4. authorisation and payload

@pytest.mark.asyncio
async def test_a_manager_is_refused_the_channels_write_as_a_whole(sa, monkeypatch, audit_bot):
    """A manager may NOT write the channels: the ACTION itself refuses.

    The console's Admin-only capability and the read-only rows are not the control: a direct caller (a
    "second caller" that never went through the console) is refused too, and nothing is written.
    """
    guild_bot(monkeypatch, sa, 10, 11)
    server = ChannelServer(locals_={"channels": {"status": 10}})
    scope = types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=False)
    ctx = ctx_for([server], roles=(), scope=scope)

    result = await sa.set_server_channels(ctx, SERVER, {"status": 11})

    assert result.ok is False and result.refused == sa.MANAGER_DENIED[sa.CHANNELS_ITEM]
    assert server.update_channels_calls == [], "a refused manager never reaches the file"


@pytest.mark.asyncio
async def test_an_admin_may_still_write_the_channels(sa, monkeypatch, audit_bot, tmp_path):
    """The positive control: an Admin is unaffected by the manager deny-list."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server], roles=("Admin",)), SERVER, {"status": 11})

    assert result.ok is True
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["status"] == 11


@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_with_the_same_sentence_as_the_dcs_write(sa, monkeypatch,
                                                                              audit_bot):
    guild_bot(monkeypatch, sa, 10)
    server = ChannelServer(locals_={"channels": {"status": 10}})
    ctx = ctx_for([server], roles=("DCS Admin",))

    result = await sa.set_server_channels(ctx, SERVER, {"status": 10})

    assert result.ok is False and result.refused == sa.ADMIN_ONLY_SENTENCE
    assert server.update_channels_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [["status"], "status=10", 42], ids=["list", "str", "int"])
async def test_a_non_mapping_payload_is_a_typed_refusal(sa, monkeypatch, audit_bot, payload):
    guild_bot(monkeypatch, sa, 10)
    server = ChannelServer(locals_={"channels": {"status": 10}})

    result = await sa.set_server_channels(ctx_for([server]), SERVER, payload)

    assert result.ok is False
    assert result.refused is None, "the skip shape is the one an unknown key uses"
    assert "must be a mapping" in result.skipped["channels"]
    assert server.update_channels_calls == []


# ---------------------------------------------------------------- 5. no state gate, typed failure

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.RUNNING, Status.PAUSED, Status.SHUTDOWN, Status.STOPPED])
async def test_a_channel_change_needs_no_stop(sa, monkeypatch, audit_bot, tmp_path, status):
    """Unlike the DCS face, a channel change is applied immediately: every state is writable."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(status=status, locals_={"channels": {"status": 10}},
                           node=node_for(tmp_path))

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"status": 11})

    assert result.ok is True, f"{status} must be writable for a channel change"
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["status"] == 11


@pytest.mark.asyncio
async def test_a_failed_write_is_a_typed_failure(sa, monkeypatch, audit_bot, tmp_path):
    """A raising ``update_channels`` never escapes as a 500 — it is a typed refusal."""
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path), fail=True)

    result = await sa.set_server_channels(ctx_for([server]), SERVER, {"status": 11})

    assert result.ok is False and result.refused and "Failed to write" in result.refused


# ------------------------------------------------------------------------------------- 6. audit

@pytest.mark.asyncio
async def test_the_write_is_audited_once_naming_the_keys(sa, monkeypatch, audit_bot, tmp_path):
    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    await sa.set_server_channels(ctx_for([server], actor="probe"), SERVER, {"status": 11})

    assert len(audit_bot.calls) == 1
    event, audited_server = audit_bot.calls[0]
    assert audit_bot.kwargs[0].get("actor") == "[web:local/probe]", audit_bot.kwargs[0]
    assert audited_server == SERVER
    assert "status" in event


@pytest.mark.asyncio
async def test_a_refusal_is_audited_too(sa, monkeypatch, audit_bot):
    guild_bot(monkeypatch, sa, 10)
    server = ChannelServer(locals_={"channels": {"status": 10}})
    ctx = ctx_for([server], roles=("DCS Admin",))

    await sa.set_server_channels(ctx, SERVER, {"status": 10})

    assert len(audit_bot.calls) == 1


# ------------------------------------------------------------ 7. registration + transport dispatch

@pytest.mark.asyncio
async def test_the_action_is_registered_and_dispatches_through_call_action(sa, monkeypatch,
                                                                          audit_bot, tmp_path):
    from core.actions import action_registry

    guild_bot(monkeypatch, sa, 10, 11)
    write_servers_yaml(tmp_path, {SERVER: {"channels": {"status": 10}}})
    server = ChannelServer(locals_={"channels": {"status": 10}}, node=node_for(tmp_path))

    assert "set_server_channels" in action_registry()
    monkeypatch.setattr(sa, "_live_bot", lambda: bot_with(10, 11))
    result = await call_action("set_server_channels", ctx_for([server]),
                               server_name=SERVER, channels={"status": 11})
    assert result.success is True
    assert read_servers_yaml(tmp_path)[SERVER]["channels"]["status"] == 11
