"""Upgrade is offered only while the NODE reports an update pending.

The node row's Upgrade control used to be offered unconditionally (the console had no signal and a
render must not issue a node call). A BACKGROUND poll (``services/webservice/upgrade.py``) asks each
node's OWN API (``Node.upgrade_pending()``) and caches the answer; the render reads ONLY the cache.
Pins: the control is offered only while the cached value is ``True`` (``False`` and UNKNOWN both
withhold it); with an empty cache the row says ``"no update check yet"`` honestly; the render issues NO
node call; the flip-and-vanish path; a raising check means UNKNOWN (it keeps the last known value only
if it was ``True`` and logs once per transition); the poller's triggers; and the re-check after an
upgrade is accepted. Driven through the real app and a stub cluster."""
from __future__ import annotations

import logging
import re
import types

import pytest

from services.webservice import upgrade as upgrade_signal
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from test_webui_node_controls import (  # noqa: F401 - fixtures imported on purpose
    ADMIN_DIALOGS, CONFIRM, NODES, UPGRADE, clean_node_registry, fleet, node_server,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)

UPGRADE_DIALOG = UPGRADE + CONFIRM
#: the controls an ``Admin`` sees on a heartbeating, in-service node while the upgrade value is
#: UNKNOWN or False: restart, shut down and *Take servers offline* — and NO Upgrade.
GATED_DIALOGS = ADMIN_DIALOGS - {UPGRADE_DIALOG}


def offered(html: str) -> set[str]:
    """The node-control paths the page actually RENDERS."""
    return set(re.findall(r'action="(/actions/node/[^"]+)"', html))


class PendingNode:
    """A node double whose ``upgrade_pending()`` records every call and answers a boolean.

    ``calls`` is the spy the render test reads: a page render must leave it EMPTY.
    """

    def __init__(self, name, *, master=False, pending=True):
        self.name = name
        self.master = master
        self.instances = {}
        self.pending = pending
        self.calls: list[str] = []

    async def restart(self):
        self.calls.append("restart")

    async def shutdown(self, rc=-2):
        self.calls.append("shutdown")

    async def upgrade(self):
        self.calls.append("upgrade")

    async def upgrade_pending(self):
        self.calls.append("upgrade_pending")
        return self.pending


class PoisonNode(PendingNode):
    """A node whose ``upgrade_pending()`` FAILS THE TEST if a render ever touches it."""

    async def upgrade_pending(self):
        raise AssertionError("a page render issued an upgrade_pending() node call")


class RaisingNode(PendingNode):
    """A node whose check always raises — the failure rule's subject."""

    async def upgrade_pending(self):
        self.calls.append("upgrade_pending")
        raise RuntimeError("the node is unreachable")


def fleet_with(node_factory, *, b_online=False):
    """Two nodes carrying one server each — ``test_webui_node_controls.fleet``'s shape, but with a
    node double the upgrade gate can drive (``upgrade_pending``)."""
    a = node_server("SRS-1", "dcs-a-01", ["Host A"])
    b = node_server("SRS-B", "dcs-b-01", ["Host B"])
    node_a = node_factory("dcs-a-01", master=True)
    node_b = node_factory("dcs-b-01") if b_online else None
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                   instances=[instance("dcs-a-01-Instance", a), instance("dcs-b-01-Instance", b)])


async def admin_page(cluster, config_dir, recorder, path=NODES):
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        return await open_page(http, path), http


# ------------------------------------------------------------------------------- 1. the gate

@pytest.mark.asyncio
async def test_the_gate_offers_upgrade_only_when_the_cache_says_pending(config_dir,
                                                                        clean_node_registry,
                                                                        recording_bot):
    """``True`` offers the glyph; ``False`` and UNKNOWN both withhold it.

    The declaration carries the gate (``NodeAction.check``) and the render reads the CACHE, never a node
    (asserted below).
    """
    cluster = fleet_with(PendingNode)
    node = cluster.nodes["dcs-a-01"]

 # UNKNOWN (empty cache): the control is NOT offered
    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG not in offered(html)

 # the node answered PENDING: the control appears
    upgrade_signal.record("dcs-a-01", True)
    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG in offered(html)
    assert 'aria-label="Upgrade node dcs-a-01"' in html

 # the node answered NOT pending: the control is gone again
    upgrade_signal.record("dcs-a-01", False)
    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG not in offered(html)


@pytest.mark.asyncio
async def test_the_unknown_state_says_so_honestly(config_dir, clean_node_registry, recording_bot):
    """With an empty cache the row says ``"no update check yet"`` rather than silently hiding it."""
    html, _ = await admin_page(fleet_with(PendingNode), config_dir, recording_bot)
    assert upgrade_signal.NOTE_NO_CHECK in html
    assert UPGRADE_DIALOG not in offered(html)
 # a KNOWN False carries no note: the control simply vanished
    upgrade_signal.record("dcs-a-01", False)
    html, _ = await admin_page(fleet_with(PendingNode), config_dir, recording_bot)
    assert upgrade_signal.NOTE_NO_CHECK not in html


@pytest.mark.asyncio
async def test_a_manager_never_sees_the_upgrade_note(config_dir, clean_node_registry,
                                                     recording_bot):
    """The note is for a caller who could otherwise be offered the control — node writes are
    Admin-only, so a manager gets neither the control nor the explanation."""
    cluster = fleet_with(PendingNode, b_online=True)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "hoster")          # a manager: no node capability at all
        html = await open_page(http, NODES)
    assert upgrade_signal.NOTE_NO_CHECK not in html
    assert UPGRADE_DIALOG not in offered(html)


# ------------------------------------------------------------- 2. the render issues no node call

@pytest.mark.asyncio
async def test_the_render_path_issues_no_node_call(config_dir, clean_node_registry, recording_bot):
    """A render with an EMPTY cache must not touch the node API — a page view calls no node.

    The node double FAILS THE TEST if ``upgrade_pending`` is called at all, so this is stricter than
    "the cache was empty on entry": it proves the render never reaches for the API even to populate
    a value it lacks.
    """
    cluster = fleet_with(PoisonNode)
    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG not in offered(html)
    assert upgrade_signal.NOTE_NO_CHECK in html
 # the poison double would have raised; assert the page still rendered and the cache is untouched
    assert upgrade_signal.snapshot() == {}


# ------------------------------------------------------------------ 3. the flip-and-vanish path

@pytest.mark.asyncio
async def test_the_control_flips_with_the_poller_and_vanishes_on_re_registration(
        config_dir, clean_node_registry, recording_bot):
    """End to end on a render: the node reports pending -> the glyph appears; after the simulated upgrade
    and re-registration it reports not-pending -> the glyph is gone.

    The POLLER drives the cache, never the render.
    """
    cluster = fleet_with(PendingNode)
    node = cluster.nodes["dcs-a-01"]
    poller = upgrade_signal.UpgradePoller()

 # service start: the first tick sweeps every node, and the node reports pending
    checked = await poller.tick({"dcs-a-01": node})
    assert checked == ("dcs-a-01",)
    assert upgrade_signal.pending("dcs-a-01") is True
    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG in offered(html), "a pending update must offer the Upgrade glyph"

 # the upgrade runs and the node goes away, then comes BACK online reporting not-pending: the
 # newcomer transition re-checks it and the cache flips
    node.pending = False
    await poller.tick({"dcs-a-01": None})          # the node dropped out of the registry
    checked = await poller.tick({"dcs-a-01": node})  #...and re-registered
    assert checked == ("dcs-a-01",), "a re-registered node is a newcomer the poller re-checks"
    assert upgrade_signal.pending("dcs-a-01") is False

    html, _ = await admin_page(cluster, config_dir, recording_bot)
    assert UPGRADE_DIALOG not in offered(html), "the pending control must be gone after the upgrade"
    assert upgrade_signal.NOTE_NO_CHECK not in html, "a KNOWN False is silent, never a note"


# ------------------------------------------------------------------------ 4. the failure rule

@pytest.mark.asyncio
async def test_a_failed_check_is_unknown_and_keeps_a_known_true(caplog):
    """A raising check → UNKNOWN, never "pending"; it KEEPS the last known value only if it was
    ``True``, and it logs ONCE PER TRANSITION."""
    checks = upgrade_signal.UpgradeChecks()

 # no prior value: a failure is UNKNOWN
    node = RaisingNode("dcs-a-01")
    with caplog.at_level(logging.WARNING, logger="services.webservice.upgrade"):
        assert await checks.check(node) is None
    assert checks.pending("dcs-a-01") is None
 #...and the failure was logged once
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert checks._entries.get("dcs-a-01").checked_at is None

 # a transient failure must not hide a REAL pending upgrade: a known True is kept
    checks.record("dcs-a-01", True)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="services.webservice.upgrade"):
        assert await checks.check(RaisingNode("dcs-a-01")) is True
    assert checks.pending("dcs-a-01") is True

 # a known False is DROPPED to UNKNOWN on failure (the safe direction)
    checks.record("dcs-a-01", False)
    checks._failed.discard("dcs-a-01")     # a fresh transition, so the first failure logs again
    with caplog.at_level(logging.WARNING, logger="services.webservice.upgrade"):
        assert await checks.check(RaisingNode("dcs-a-01")) is None
    assert checks.pending("dcs-a-01") is None


@pytest.mark.asyncio
async def test_a_repeated_failure_logs_once_per_transition(caplog):
    """The latch: the FIRST failure of a node is loud, the rest stay quiet until a success clears it."""
    checks = upgrade_signal.UpgradeChecks()
    with caplog.at_level(logging.WARNING, logger="services.webservice.upgrade"):
        await checks.check(RaisingNode("dcs-a-01"))
        await checks.check(RaisingNode("dcs-a-01"))
        await checks.check(RaisingNode("dcs-a-01"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "a node that is simply down must not spam the log once a minute"
 # a success clears the latch, so a LATER failure logs once again
    await checks.check(PendingNode("dcs-a-01", pending=False))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="services.webservice.upgrade"):
        await checks.check(RaisingNode("dcs-a-01"))
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


# -------------------------------------------------------------------- 5. the poller's triggers

@pytest.mark.asyncio
async def test_the_poller_sweeps_on_the_first_tick_and_every_six_hours():
    """Trigger (a) service start = the first tick's full sweep; trigger (c) the six-hour re-sweep."""
    checks = upgrade_signal.UpgradeChecks()
    now = [0.0]
    poller = upgrade_signal.UpgradePoller(checks, sweep_seconds=100, clock=lambda: now[0])
    a, b = PendingNode("a"), PendingNode("b")

 # first tick: BOTH nodes are swept, even though neither is a "newcomer"
    assert await poller.tick({"a": a, "b": b}) == ("a", "b")
    a.calls.clear(), b.calls.clear()

 # an ordinary tick well inside the window checks NEITHER (no newcomer, sweep not due)
    now[0] = 30
    assert await poller.tick({"a": a, "b": b}) == ()
    assert a.calls == [] and b.calls == []

 # the six-hour sweep comes due: every node is re-checked
    now[0] = 100
    assert await poller.tick({"a": a, "b": b}) == ("a", "b")


@pytest.mark.asyncio
async def test_the_poller_checks_a_newcomer_and_leaves_an_absent_node_alone():
    """Trigger (b): a node that comes ONLINE is a newcomer; a node the cluster cannot reach is not."""
    checks = upgrade_signal.UpgradeChecks()
    poller = upgrade_signal.UpgradePoller(checks)
    a = PendingNode("a")

 # a node that is None (known, unreachable) is NOT a newcomer and is NOT checked
    assert await poller.tick({"a": None}) == ()
    assert upgrade_signal.pending("a") is None

 # the same node resolving to an object is a newcomer → checked
    assert await poller.tick({"a": a}) == ("a",)


# ------------------------------------------------------ 6. after an Upgrade is accepted

@pytest.mark.asyncio
async def test_a_successful_upgrade_rechecks_that_node(config_dir, clean_node_registry,
                                                       recording_bot):
    """Trigger (d): the node route re-checks the node ONCE the Upgrade has been accepted.

    The action's own guard runs ``upgrade_pending()`` too, so the honest count is the ACTION's
    check plus the route's scheduled re-check — the point is that the cache's ``checked_at`` moves
    without another poll tick.
    """
    import asyncio

    cluster = fleet_with(PendingNode)
    node = cluster.nodes["dcs-a-01"]
    upgrade_signal.record("dcs-a-01", True)
    before = upgrade_signal.checked_at("dcs-a-01")

    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        assert UPGRADE_DIALOG in offered(page), "the control must be offered for this path"
        dialog = await http.post(UPGRADE_DIALOG,
                                 data={"node": "dcs-a-01", "_csrf_token": token_of(page)})
        match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
        accepted = await http.post(UPGRADE,
                                   data={"node": "dcs-a-01", "_csrf_token": token_of(dialog.text),
                                         "_confirm_token": match.group(1)})
        assert accepted.status_code == 303
 # let the fire-and-forget re-check run
        await asyncio.sleep(0.05)

    assert "upgrade" in node.calls, "the action ran"
    assert "upgrade_pending" in node.calls, "the node was re-checked after the upgrade was accepted"
    assert upgrade_signal.checked_at("dcs-a-01") != before, "the cache was refreshed, not left stale"


# ------------------------------------------------------------------------ 7. the dialog copy

@pytest.mark.asyncio
async def test_the_dialog_reports_when_the_value_was_last_checked(config_dir, clean_node_registry):
    """The dialog may show when the cached value was produced, so a stale answer is visible."""
    from datetime import datetime, timezone

    cluster = fleet_with(PendingNode)
    upgrade_signal.record("dcs-a-01", True, at=datetime(2026, 9, 30, 15, 4, tzinfo=timezone.utc))
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        dialog = await http.post(UPGRADE_DIALOG,
                                 data={"node": "dcs-a-01", "_csrf_token": token_of(page)})
    assert dialog.status_code == 200
    assert "Last update check: 2026-09-30 15:04 UTC." in dialog.text
 # the rewritten copy no longer claims the console "cannot tell"
    assert "cannot tell whether a newer version is available" not in dialog.text
    assert "reports an update pending" in dialog.text
