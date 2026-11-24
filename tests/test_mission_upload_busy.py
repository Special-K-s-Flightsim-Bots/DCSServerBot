"""The upload's four transports refuse a busy server, and a refused upload writes nothing.

``LOADING`` and ``SHUTTING_DOWN`` are the two states in which DCS writes ``serverSettings.lua``
itself, so an upload landing then would be recorded in a settings write DCS overwrites. This file
pins the SURFACES over the shipped bodies:

* the ACTION refuses before the name check and before a byte is staged — one audit entry, no
  ``files`` row, the ONE writer never reached;
* the REST endpoint hands that refusal to the caller as a client error, sentence included;
* ``MissionUploadHandler.handle_attachment`` — the Discord attachment, which never sees the action —
  answers the sentence and does NOT retry with ``force=True``;
* the bot's, the plugin's and the console's copies of the sentence are the same string, so an
  operator cannot tell which door refused.
"""
from __future__ import annotations

import logging
import types
from typing import Any, cast

import pytest
from fastapi import HTTPException

import core.actions as core_actions
import plugins.mission.actions as mission_actions
from core import Status, UploadStatus
from core.data.impl.serverimpl import (
    MISSION_LIST_BUSY_REFUSAL as BOT_REFUSAL,
)
import core.data.const as core_const
from plugins.mission.upload import MissionUploadHandler
from plugins.restapi.commands import RestAPI
from services.webservice.pages import actions as console_actions

# the upload suite's own doubles, reused so both files drive ONE shape of server
from test_mission_seam import (  # noqa: F401 - fixtures ``actions`` and ``rest_bus`` ride along
    FakeUpload,
    RestDouble,
    ServerDouble,
    actions,
    rest_bus,
    run,
)

BUSY_STATES = (Status.LOADING, Status.SHUTTING_DOWN)


# ------------------------------------------------------------------------------ the one sentence

def test_the_bot_the_plugin_and_the_console_refuse_with_the_same_sentence():
    """ONE object, not three equal strings: every surface imports :mod:`core.data.const`'s copy."""
    assert mission_actions.MISSION_LIST_BUSY_REFUSAL is BOT_REFUSAL
    assert console_actions.MISSION_LIST_BUSY_REFUSAL is BOT_REFUSAL
    assert BOT_REFUSAL is core_const.MISSION_LIST_BUSY_REFUSAL
    assert "MISSION_LIST_BUSY_REFUSAL" in mission_actions.__all__, "the plugin re-exports the wording"


def test_the_wording_is_defined_once_in_the_whole_source_tree():
    """A second literal is a second place to edit: the sentence lives in ONE module, imported by all."""
    import pathlib

    sentence = "The mission list cannot be changed while the server is starting up"
    root = pathlib.Path(__file__).parent.parent
    holders = sorted(p.relative_to(root).as_posix()
                     for sub in ("core", "plugins", "services")
                     for p in (root / sub).rglob("*.py")
                     if sentence in p.read_text(encoding="utf-8", errors="ignore"))
    assert holders == ["core/data/const.py"], f"the refusal is written in {holders}"

    deletable = "Missions cannot be removed while the server is loading."
    holders = sorted(p.relative_to(root).as_posix()
                     for sub in ("core", "plugins", "services")
                     for p in (root / sub).rglob("*.py")
                     if deletable in p.read_text(encoding="utf-8", errors="ignore"))
    assert holders == ["core/data/const.py"], f"the bulk-remove refusal is written in {holders}"


def test_the_refusal_names_both_states_plainly():
    assert BOT_REFUSAL == ("The mission list cannot be changed while the server is starting up "
                           "or shutting down.")


# ---------------------------------------------------------------------------------- 1. the action

class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry (not the no-op)."""

    def __init__(self):
        self.entries: list[dict] = []

    async def audit(self, event, server=None, **kwargs):
        self.entries.append({"event": event, "server": getattr(server, "name", None)})


@pytest.fixture
def audit_bot(monkeypatch):
    bot = RecordingBot()
    monkeypatch.setattr(core_actions, "_resolve_bot", lambda ctx: bot)
    return bot


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
async def test_the_action_refuses_in_a_busy_state_and_stages_nothing(actions, audit_bot, status):
    server = ServerDouble(configured=[])
    server.status = status

    result = await run(actions, "upload_mission", server, name="New.miz", source=b"MISSIONBYTES")

    assert result.success is False
    assert result.message == BOT_REFUSAL
    assert server.uploads == [], "the ONE writer was never reached"
    assert server.node.staged == [], "no bytes were staged"
    assert server.node.table.count == 0, "no ``files`` row was left behind"
    assert len(audit_bot.entries) == 1, "a refused attempt is still one audit entry"
    assert audit_bot.entries[0]["event"] == BOT_REFUSAL
    assert audit_bot.entries[0]["server"] == server.name


@pytest.mark.asyncio
async def test_the_action_still_uploads_in_shutdown(actions):
    """CONTROL: the ordinary path — a server whose process is down — is untouched."""
    server = ServerDouble(configured=["/missions/New.miz"])
    server.status = Status.SHUTDOWN

    result = await run(actions, "upload_mission", server, name="New.miz", source=b"X")

    assert result.success is True
    assert server.uploads and server.uploads[0][0] == "New.miz"


@pytest.mark.asyncio
async def test_a_busy_answer_from_the_writer_is_the_sentence(actions, audit_bot):
    """The state flips between the pre-check and the write: the writer answers ``BUSY``.

    The operator reads the ONE sentence — never ``Failed to upload mission 'X' ... (BUSY)`` — the
    attempt keeps its single audit entry, and the staged row is discarded by the existing ``finally``.
    """
    server = ServerDouble(configured=[], rc=UploadStatus.BUSY)
    server.status = Status.RUNNING

    result = await run(actions, "upload_mission", server, name="New.miz", source=b"MISSIONBYTES")

    assert result.success is False
    assert result.message == BOT_REFUSAL
    assert result.upload_status == "BUSY"
    assert len(server.uploads) == 1, "the writer WAS reached — this is the race window"
    assert server.node.staged, "the bytes were staged before the writer answered"
    assert server.node.table.count == 0, "the staged row was discarded"
    assert len(audit_bot.entries) == 1
    assert audit_bot.entries[0]["event"] == BOT_REFUSAL


@pytest.mark.asyncio
async def test_an_ok_answer_from_the_writer_still_succeeds(actions, audit_bot):
    """CONTROL: the same path with ``OK`` still reports a success and one audit entry."""
    server = ServerDouble(configured=["/missions/New.miz"], rc=UploadStatus.OK)
    server.status = Status.RUNNING

    result = await run(actions, "upload_mission", server, name="New.miz", source=b"MISSIONBYTES")

    assert result.success is True
    assert result.message == "Mission 'New.miz' uploaded to server 'SRS-1'."
    assert server.node.table.count == 0
    assert len(audit_bot.entries) == 1


# ------------------------------------------------------------------------------------ 2. the REST

@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
async def test_rest_hands_the_sentence_to_the_caller(actions, rest_bus, status):
    server = ServerDouble(configured=[])
    server.status = status
    rest_bus["bus"] = types.SimpleNamespace(servers={server.name: server})
    rest = RestDouble(server)

    with pytest.raises(HTTPException) as caught:
        await RestAPI.mission_upload(cast(RestAPI, rest), server_name=server.name,
                                     file=cast(Any, FakeUpload(b"X")),
                                     filename="New.miz", load_after=False)

    assert caught.value.status_code == 400
    assert caught.value.detail == BOT_REFUSAL
    assert server.uploads == []
    assert server.node.table.count == 0


# -------------------------------------------------------------------------- 3. the Discord handler

class _Channel:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message, **kwargs):
        self.sent.append(message)


def _handler(server, *, rc):
    """``MissionUploadHandler``'s slice, driven through the shipped ``handle_attachment`` body."""
    channel = _Channel()
    calls: list[tuple] = []

    async def upload_mission(filename, url, *, missions_dir=None, force=False):
        calls.append((filename, url, force))
        return rc

    async def get_context(message):
        return types.SimpleNamespace()

    server.uploadMission = upload_mission
    handler = types.SimpleNamespace(
        server=server,
        channel=channel,
        overwrite=False,
        message=types.SimpleNamespace(),
        log=logging.getLogger("test.mission.upload_busy"),
        bot=types.SimpleNamespace(get_context=get_context),
    )
    return cast(MissionUploadHandler, handler), channel, calls


@pytest.mark.asyncio
async def test_the_discord_attachment_answers_the_sentence_and_does_not_retry():
    server = ServerDouble(configured=[])
    handler, channel, calls = _handler(server, rc=UploadStatus.BUSY)
    att = types.SimpleNamespace(filename="New.miz", url="http://example/New.miz")

    rc = await MissionUploadHandler.handle_attachment(handler, "/missions", cast(Any, att))

    assert rc == UploadStatus.BUSY
    assert channel.sent == [BOT_REFUSAL], "the operator reads the sentence, never 'Error ...: BUSY'"
    assert len(calls) == 1, "the ``force=True`` retry must not fire"
    assert calls[0][1] == "http://example/New.miz"


@pytest.mark.asyncio
async def test_the_discord_attachment_still_reports_a_success():
    """CONTROL: an accepted upload of an existing mission still answers and does not retry."""
    server = ServerDouble(configured=[])
    handler, channel, calls = _handler(server, rc=UploadStatus.OK)
    att = types.SimpleNamespace(filename="New.miz", url="http://example/New.miz")

    rc = await MissionUploadHandler.handle_attachment(handler, "/missions", cast(Any, att))

    assert rc == UploadStatus.OK
    assert "uploaded to server" in channel.sent[-1]
    assert len(calls) == 1
