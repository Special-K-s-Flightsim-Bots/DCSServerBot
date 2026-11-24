"""A deferred preset pick is ALL OR NOTHING, and a refusal is announced where it was asked.

The deferred path (``Scheduler._run_with_presets``) resolves the entries the operator picked minutes
or hours earlier. A preset file that vanished in between must not halve the pick: every entry is
resolved first, one refusal discards the whole pick, the mission loads unchanged (the pick-less
path), and the operator is told in the channel the deferral was requested from.

Pins:
* a two-entry pick whose second file is gone applies zero documents and announces one sentence;
* a pick whose entries all resolve applies every document, in the pick's order;
* a BARE name that is absent refuses the whole pick and names the configured file;
* a pick-less deferred load is untouched: no announcement, and the channel is never consulted.
"""
from __future__ import annotations

import os

import logging
import types

import pytest

from core import Status
from plugins.scheduler.commands import Scheduler

NODE = "dcs-a-01"
SERVER = "SRS-1"
#: host-spelled: the bot joins and normalises it, so a raw POSIX root never matches on Windows
MISSION = os.path.join(os.path.abspath("/missions"), "Training.miz")

PRESETS = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
Pair:
  modify:
    weather:
      clouds:
        base: 500
"""

SECOND = """\
Winter:
  modify:
    weather:
      clouds:
        base: 200
"""


class FakeChannel:
    """The channel a deferred pick was requested from: records what was said in it."""

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, content: str = "", **kwargs):
        self.sent.append(content)
        return None


class FakeBot:
    def __init__(self, channels: dict[int, FakeChannel]):
        self.channels = channels
        self.lookups: list[int] = []

    def get_channel(self, channel_id: int):
        self.lookups.append(channel_id)
        return self.channels.get(channel_id)


class PickServer:
    """The server object the deferred path touches, recording every launch call."""

    def __init__(self, status=Status.RUNNING, mission: str = MISSION):
        self.status = status
        self.name = SERVER
        self.locals = {"mission_rewrite": True}
        self.current_mission = None
        self._mission = mission
        self.calls: list[tuple[str, dict]] = []
        self.modified: list[tuple[str, list, bool]] = []

    async def getMissionList(self):
        return [self._mission]

    async def modifyMission(self, filename, preset, use_orig=True):
        self.modified.append((filename, preset, use_orig))
        return filename

    async def replaceMission(self, idx, path):
        return [self._mission]

    async def stop(self):
        self.calls.append(("stop", {}))

    async def start(self, **kwargs):
        self.calls.append(("start", kwargs))

    async def startup(self, *args, **kwargs):
        self.calls.append(("startup", {"args": args, **kwargs}))

    async def loadMission(self, *args, **kwargs):
        self.calls.append(("loadMission", {"args": args, **kwargs}))
        return True


class PickNode:
    """The node's preset discovery: ``list_files`` + ``read_file`` over real files on disk."""

    def __init__(self, config_dir, files: dict[str, str]):
        self.config_dir = str(config_dir)
        self.name = NODE
        self.locals = {}
        self.master = True
        self._files = {str(path): content for path, content in files.items()}
        self.list_calls = 0

    async def list_files(self, config_dir, pattern=None):
        self.list_calls += 1
        return [(path, len(content), 1.0) for path, content in self._files.items()]

    async def read_file(self, path):
        return self._files[str(path)].encode("utf-8")


def build(tmp_path, *, server_status=Status.RUNNING, files=None, channels=None):
    """A scheduler cog with a node whose preset files are the ones in *files*."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    on_disk: dict[str, str] = {}
    for name, content in (files if files is not None else {"presets.yaml": PRESETS}).items():
        path = config_dir / name
        path.write_text(content, encoding="utf-8")
        on_disk[str(path)] = content
    node = PickNode(config_dir, on_disk)
    channel = FakeChannel()
    bot = FakeBot({1: channel} if channels is None else channels)
    cog = types.SimpleNamespace(
        node=node,
        bot=bot,
        log=logging.getLogger("test.scheduler.deferred_pick"),
        config_dir=node.config_dir,
    )
    cog.__cog_name__ = "Scheduler"
    # the double is not a Scheduler: bind the members the deferred path calls on itself, when the
    # module carries them (an older module may not, which is what makes the pins RED on it)
    if (helper := getattr(Scheduler, "_preset_file_text", None)) is not None:
        cog._preset_file_text = helper
    for name in ("_announce_refused_pick", "_run_without_presets"):
        if (member := getattr(Scheduler, name, None)) is not None:
            setattr(cog, name, types.MethodType(member, cog))
    server = PickServer(status=server_status)
    server.node = node
    return cog, server, channel, bot, on_disk


def rconf(file_path, *entries, **extra):
    conf = {
        "method": "restart",
        "settings": list(entries),
        "presets": file_path,
        "use_orig": False,
        "channel": 1,
    }
    conf.update(extra)
    return conf


# ------------------------------------------------- 1. all or nothing: a vanished file halves nothing

@pytest.mark.asyncio
async def test_a_refused_entry_applies_zero_documents_and_announces_one_sentence(tmp_path):
    files = {"presets.yaml": PRESETS, "presets_winter.yaml": SECOND}
    cog, server, channel, _bot, on_disk = build(tmp_path, files=files)
    first = f"config/presets.yaml\nConfigured"
    gone_label = "config/presets_gone.yaml"
    second = f"{gone_label}\nSparrow"

    await Scheduler._run_with_presets(  # type: ignore[arg-type]
        cog, server, rconf(next(iter(on_disk)), *[first, second]), 1, False)

    assert server.modified == [], "no preset from a refused pick may be applied"
    assert len(channel.sent) == 1, "a refused pick is announced exactly once"
    sentence = channel.sent[0]
    assert "Sparrow" in sentence and gone_label in sentence, \
        "the announcement must name the preset and the file that went missing"
    assert sentence == ('No preset was applied: "Sparrow" (config/presets_gone.yaml) could not be '
                        'found; the mission will be restarted unchanged.'), \
        "the operator's sentence must state the missing preset, its file, and what happens instead"


@pytest.mark.asyncio
async def test_a_refused_load_still_loads_the_mission_unchanged(tmp_path):
    files = {"presets.yaml": PRESETS}
    cog, server, channel, _bot, on_disk = build(tmp_path, files=files)
    entry = "config/presets_gone.yaml\nSparrow"

    await Scheduler._run_with_presets(  # type: ignore[arg-type]
        cog, server, rconf(next(iter(on_disk)), entry, run_extensions=False), 1, False)

    assert server.modified == []
    assert server.calls == [("loadMission", {"args": (1,), "modify_mission": False,
                                             "use_orig": False, "no_reload": False})], \
        "a refused pick must load the mission the way the pick-less path does"
    assert len(channel.sent) == 1


# ------------------------------------------------------------ 2. every entry resolves: all applied

@pytest.mark.asyncio
async def test_a_pick_that_resolves_applies_every_document_in_order(tmp_path):
    files = {"presets.yaml": PRESETS}
    cog, server, channel, _bot, on_disk = build(tmp_path, files=files)
    preset_path = [path for path in on_disk if path.endswith("presets.yaml")][0]
    picked = ["config/presets.yaml\nConfigured", "config/presets.yaml\nPair"]

    await Scheduler._run_with_presets(  # type: ignore[arg-type]
        cog, server, rconf(preset_path, *picked), 1, False)

    assert len(server.modified) == 1, "one mission rewrite carries the whole pick"
    _filename, documents, _use_orig = server.modified[0]
    assert len(documents) == 2, "both picked presets apply"
    assert [doc["modify"]["weather"]["clouds"]["base"] for doc in documents] == [1000, 500], \
        "the documents follow the pick's order"
    assert channel.sent == [], "a pick that resolves announced nothing"


# ---------------------------------------------------------- 3. a bare absent name refuses the pick

@pytest.mark.asyncio
async def test_a_bare_absent_name_refuses_the_whole_pick(tmp_path):
    files = {"presets.yaml": PRESETS}
    cog, server, channel, _bot, on_disk = build(tmp_path, files=files)
    preset_path = [path for path in on_disk if path.endswith("presets.yaml")][0]

    await Scheduler._run_with_presets(  # type: ignore[arg-type]
        cog, server, rconf(preset_path, "Configured", "Missing"), 1, False)

    assert server.modified == [], "a bare absent name is part of the pick and refuses all of it"
    assert len(channel.sent) == 1
    assert "Missing" in channel.sent[0] and "config/presets.yaml" in channel.sent[0], \
        "the bare name is announced against the configured preset file"


# ------------------------------------------------------ 4. a pick-less deferred load is unchanged

@pytest.mark.asyncio
async def test_a_pickless_deferred_load_announces_nothing_and_keeps_its_shape(tmp_path):
    cog, server, _channel, bot, _on_disk = build(tmp_path)
    conf = {"method": "restart", "presets": "unused", "use_orig": False, "channel": 1}

    await Scheduler._run_without_presets(cog, server, conf, 1)  # type: ignore[arg-type]

    assert bot.lookups == [], "a pick-less load never consults the deferred channel"
    assert server.calls == [("loadMission", {"args": (1,), "modify_mission": True,
                                             "use_orig": False, "no_reload": False})]
