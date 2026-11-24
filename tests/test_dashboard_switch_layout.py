"""A master/agent switch must NOT cycle the Dashboard service.

``Dashboard.switch`` used to call ``self.stop()`` + ``self.start()``. Both take the "direct" path
in ``core/services/base.py``, which runs a registry cascade: ``stop_service`` drops the singleton
and ``start_service`` builds a FRESH instance. The object the switch ran on is then orphaned while
the new one is registered — and both instances start a renderer, so two ``Live(screen=True)``
fight over one terminal (the winner's screen is wiped when the orphan tears its own down) and
``hook_logging`` runs twice, leaving the console with queue handlers and no console handler.

The layout is the only role-dependent part (a master adds the node column and the node widgets),
so the switch flags it dirty and the running update loop rebuilds it — the same rebuild it already
does when the server count changes.
"""
from __future__ import annotations

import asyncio

import pytest

from services.dashboard import service as dashboard_module
from services.dashboard.service import Dashboard


def _dashboard() -> Dashboard:
    """A Dashboard with ``__init__`` skipped: these tests drive two shipped methods only."""
    instance = object.__new__(Dashboard)
    instance._layout_dirty = False
    instance.layout = "initial"
    instance.stop_event = asyncio.Event()
    instance.update_task = None
    instance.running = True
    return instance


def test_switch_revives_a_renderer_that_died_with_an_exception():
    """The old stop/start revived a crashed loop as a side effect; the flag alone must keep that."""

    async def scenario() -> tuple[Dashboard, asyncio.Future]:
        dashboard = _dashboard()

        async def crashed():
            raise RuntimeError("boom")

        dead = asyncio.ensure_future(crashed())
        await asyncio.sleep(0)
        dashboard.update_task = dead
        dashboard.stop_event.set()          # what a crashed update loop leaves behind

        async def render():
            return None

        dashboard.update = render      # type: ignore[method-assign]
        await Dashboard.switch(dashboard, True)
        await dashboard.update_task
        return dashboard, dead

    dashboard, dead = asyncio.run(scenario())
    assert dashboard._layout_dirty is True
    assert dashboard.update_task is not dead, "the dead renderer was not replaced"
    assert dashboard.stop_event.is_set() is False, "the crashed loop's stop flag was not cleared"


def test_switch_flags_the_layout_instead_of_cycling_the_service():
    dashboard = _dashboard()
    calls: list[str] = []

    async def cycle(*args, **kwargs):
        calls.append("cycle")
        raise AssertionError("switch must not stop/start the service: the registry cascade "
                             "replaces the singleton and a second renderer starts")

    dashboard.stop = cycle            # type: ignore[method-assign]
    dashboard.start = cycle           # type: ignore[method-assign]

    asyncio.run(Dashboard.switch(dashboard, True))

    assert calls == []
    assert dashboard._layout_dirty is True


class _FakeLive:
    """Stand-in for rich's Live: records the layouts pushed to it, renders nothing."""

    def __init__(self, layout, **kwargs):
        self.layout = layout
        self.updates: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def update(self, layout):
        self.updates.append(layout)


class _StopAfter:
    """``stop_event`` double that lets the update loop run a fixed number of iterations."""

    def __init__(self, iterations: int):
        self.remaining = iterations

    def is_set(self) -> bool:
        self.remaining -= 1
        return self.remaining < 0


def _run_update(dashboard: Dashboard, monkeypatch: pytest.MonkeyPatch, iterations: int) -> _FakeLive:
    created: list[_FakeLive] = []

    class RecordingLive(_FakeLive):
        def __init__(self, layout, **kwargs):
            super().__init__(layout, **kwargs)
            created.append(self)

    monkeypatch.setattr(dashboard_module, "Live", RecordingLive)
    monkeypatch.setattr(dashboard_module.asyncio, "sleep", _no_sleep)
    dashboard.stop_event = _StopAfter(iterations)  # type: ignore[assignment]
    asyncio.run(dashboard.update())
    return created[0]


async def _no_sleep(_delay):
    return None


def test_the_update_loop_rebuilds_the_layout_when_the_role_changed(monkeypatch):
    dashboard = _dashboard()
    dashboard.bus = type("Bus", (), {"servers": {}})()
    dashboard.create_layout = lambda: f"layout-{id(dashboard)}"   # type: ignore[method-assign]
    dashboard._layout_dirty = True

    live = _run_update(dashboard, monkeypatch, iterations=2)

    assert live.updates, "the role change never reached the screen"
    assert dashboard._layout_dirty is False


def test_the_update_loop_leaves_the_layout_alone_without_a_role_change(monkeypatch):
    dashboard = _dashboard()
    dashboard.bus = type("Bus", (), {"servers": {}})()

    def create_layout():                     # a rebuild here would clear the screen every tick
        raise AssertionError("the layout was rebuilt without a role or server-count change")

    dashboard.create_layout = create_layout   # type: ignore[method-assign]

    live = _run_update(dashboard, monkeypatch, iterations=2)

    assert live.updates == []
