"""A node's cached DCS answer must stop claiming an update is waiting, whatever updated it.

The console renders a CACHED answer, and an update that runs outside the console (a bot-launch
autoupdate, ``/dcs update`` from Discord, an update on another node) moves nothing the cache can see:
the amber chip and the highlighted DCS glyph then survive the finished update, which is reported as
"it still says an update is pending". Two triggers close that gap — the node's own
``after_dcs_update`` hook for an update this process ran, and a short re-read of an answer that still
says an update is WAITING for one this process did not.

Pins: the hook is registered with the frontend on and not with it off; the hook refreshes the cache;
a waiting node is re-checked on the amber beat although no sweep is due; a current node is NOT
re-checked between sweeps; the beat stops once the answer clears; and an answer kept by a failed check
is re-read until the node answers again.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from services.webservice import dcs_update
from test_webui_dcs_update import BLOCK, DcsNode  # noqa: F401 - the shared node double
from test_webui_frontend_switch import boot, cancel_tasks, run_start

NODE = "dcs-a-01"


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The config validator reads ``COMMAND_LINE_ARGS.node``; this interpreter is not the bot."""
    import core.commandline as commandline

    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def waiting(newer="2.9.9.111") -> DcsNode:
    """A node whose installed build is older than the one the publisher offers."""
    return DcsNode(NODE, newer=newer)


# ------------------------------------------------- 1. an update this process ran

@pytest.mark.asyncio
async def test_the_post_update_hook_replaces_the_cached_answer(tmp_path, monkeypatch):
    """The node fires ``after_dcs_update``; the console's cache is refreshed from the node itself."""
    checked: list[str] = []
    from services.webservice.dcs_update import DcsChecks

    original = DcsChecks.check

    async def wrapped(self, node, *, declared=True):
        checked.append(node.name)
        node.newer = None                      # the update has just installed the newest build
        return await original(self, node, declared=declared)

    monkeypatch.setattr(DcsChecks, "check", wrapped)
    dcs_update.reset()

    svc = boot(tmp_path, frontend="true")
    await run_start(svc)
    try:
        assert svc.name in svc.node.after_update, "the console did not register the update hook"
        node = waiting()
        svc.node = node                     # the hook reports THIS process's own node
        await svc.after_dcs_update()        # the node calls its hooks; the console re-reads
        for _ in range(40):
            if checked:
                break
            await asyncio.sleep(0.01)
        assert checked == [node.name], f"the hook did not re-check the node: {checked}"
        assert dcs_update.available(node.name) is False, "the pre-update answer survived the hook"
    finally:
        await cancel_tasks(svc)
        dcs_update.reset()


@pytest.mark.asyncio
async def test_the_hook_is_registered_only_with_the_frontend_on(tmp_path):
    """No console, no cache to refresh: registering the hook there would be dead work."""
    off = boot(tmp_path / "off", frontend="false")
    await run_start(off)
    assert off.node.after_update == {}, "the hook was registered with no console to serve"
    await cancel_tasks(off)

    on = boot(tmp_path / "on", frontend="true")
    await run_start(on)
    assert on.node.after_update.get(on.name) is not None
    assert "after_dcs_update" not in on.node.before_update
    await cancel_tasks(on)


# ------------------------------------------------- 2. an update this process did not run

@pytest.mark.asyncio
async def test_a_waiting_node_is_re_read_on_the_amber_beat():
    """No sweep due and not a newcomer: the WAITING answer alone brings the node back."""
    checks = dcs_update.DcsChecks()
    now = [1000.0]
    poller = dcs_update.DcsUpdatePoller(checks, sweep_seconds=10_000, amber_seconds=120,
                                        clock=lambda: now[0])
    node = waiting()
    declared = {NODE: dict(BLOCK)}

    assert await poller.tick({NODE: node}, declared) == (NODE,)      # the service-start sweep
    assert checks.available(NODE) is True

    now[0] += 60
    assert await poller.tick({NODE: node}, declared) == (), "re-read before the beat was due"
    now[0] += 60
    assert await poller.tick({NODE: node}, declared) == (NODE,), "the amber answer was never re-read"


@pytest.mark.asyncio
async def test_a_current_node_is_not_re_read_between_sweeps():
    """The beat is confined to amber nodes: a current installation costs one call per sweep."""
    checks = dcs_update.DcsChecks()
    now = [1000.0]
    poller = dcs_update.DcsUpdatePoller(checks, sweep_seconds=10_000, amber_seconds=120,
                                        clock=lambda: now[0])
    node = DcsNode(NODE, newer=None)
    declared = {NODE: dict(BLOCK)}

    assert await poller.tick({NODE: node}, declared) == (NODE,)
    assert checks.available(NODE) is False
    calls = len(node.calls)
    for _ in range(10):
        now[0] += 120
        assert await poller.tick({NODE: node}, declared) == ()
    assert len(node.calls) == calls, "a current node was re-read on the amber beat"


@pytest.mark.asyncio
async def test_the_beat_stops_once_the_answer_clears():
    """The update lands between two ticks: the glyph clears and the beat goes quiet."""
    checks = dcs_update.DcsChecks()
    now = [1000.0]
    poller = dcs_update.DcsUpdatePoller(checks, sweep_seconds=10_000, amber_seconds=120,
                                        clock=lambda: now[0])
    node = waiting()
    declared = {NODE: dict(BLOCK)}

    assert await poller.tick({NODE: node}, declared) == (NODE,)
    node.newer = None                       # the update completed elsewhere
    now[0] += 120
    assert await poller.tick({NODE: node}, declared) == (NODE,)
    assert checks.available(NODE) is False, "the amber answer survived the re-read"
    for _ in range(5):
        now[0] += 120
        assert await poller.tick({NODE: node}, declared) == (), "the beat kept polling a current node"


@pytest.mark.asyncio
async def test_a_failed_check_that_keeps_an_update_is_re_read_until_the_node_answers():
    """A node restarting THROUGH its update keeps the last known ``True`` — so the beat must retry.

    The failure rule is deliberate: a transient error must not forget an update that may be in flight.
    Without the beat, that kept ``True`` would hold the amber glyph until the next six-hour sweep.
    """
    checks = dcs_update.DcsChecks()
    now = [1000.0]
    poller = dcs_update.DcsUpdatePoller(checks, sweep_seconds=10_000, amber_seconds=120,
                                        clock=lambda: now[0])
    node = waiting()
    declared = {NODE: dict(BLOCK)}
    assert await poller.tick({NODE: node}, declared) == (NODE,)

    node.fail = True                        # the updater holds the installation
    now[0] += 120
    assert await poller.tick({NODE: node}, declared) == (NODE,)
    assert checks.available(NODE) is True, "a transient failure dropped a real update"

    node.fail = False
    node.newer = None                       # the node is back, and current
    now[0] += 120
    assert await poller.tick({NODE: node}, declared) == (NODE,)
    assert checks.available(NODE) is False
