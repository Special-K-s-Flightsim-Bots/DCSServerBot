"""``ActionResult.code``: the machine-readable outcome, set at the seam's own refusal points.

The code exists so a transport maps an outcome to a status without parsing English. It is a FIELD on
the result and never a key of :meth:`ActionResult.to_dict` — the console and the Discord renderer
consume that envelope, and its shape is pinned here so neither gains a key.

Every path below is driven through the REAL :func:`core.actions.call_action` /
:func:`core.actions.read_action`. Only the registry is a fake: the seams themselves, the in-flight
guard and the exception typing are the shipped code.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from core import actions as actions_module
from core.action_results import (
    ACTION_FAILED,
    ACTION_UNAVAILABLE,
    TARGET_BUSY,
    ActionResult,
    ServerControlResult,
)
from core.actions import ActionContext, call_action, read_action, reset_in_flight


@pytest.fixture
def registry(monkeypatch):
    """A registry of this test's own actions, restored afterwards (it is process state)."""
    fakes: dict = {}
    monkeypatch.setattr(actions_module, "_ACTIONS", fakes)
    reset_in_flight()
    try:
        yield fakes
    finally:
        reset_in_flight()


@pytest.fixture
def ctx():
    return ActionContext(node=None, bus=types.SimpleNamespace(servers={}))


# ── the wire contract of the three constants ──────────────────────────────

def test_the_three_codes_are_the_documented_literals():
    """A transport keys its status map on these strings, so the literals are the contract."""
    assert ACTION_UNAVAILABLE == "action_unavailable"
    assert TARGET_BUSY == "target_busy"
    assert ACTION_FAILED == "action_failed"
    assert len({ACTION_UNAVAILABLE, TARGET_BUSY, ACTION_FAILED}) == 3, (
        "three distinct codes: each refusal owes its caller a different status")


# ── refusal 1: the action is not registered ───────────────────────────────

@pytest.mark.asyncio
async def test_call_action_marks_an_absent_action_unavailable(registry, ctx):
    result = await call_action("no_such_action", ctx, server_name="SRS-1")

    assert result.success is False
    assert result.code == ACTION_UNAVAILABLE
    assert result.message == "Action 'no_such_action' is not available in this installation.", \
        "the message is unchanged — the code travels on the field, never in the sentence"


@pytest.mark.asyncio
async def test_read_action_marks_an_absent_action_unavailable(registry, ctx):
    result = await read_action("no_such_action", ctx, server_name="SRS-1")

    assert result.success is False
    assert result.code == ACTION_UNAVAILABLE
    assert result.message == "Action 'no_such_action' is not available in this installation."


# ── refusal 2: the in-flight guard ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_in_flight_guard_marks_the_second_call_busy(registry, ctx):
    """A REAL second call against a target an action is running on — no hand-set guard state."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def stop_server(ctx, server_name: str) -> ServerControlResult:
        started.set()
        await release.wait()
        return ServerControlResult(success=True, server_name=server_name)

    registry["stop_server"] = stop_server
    first = asyncio.create_task(call_action("stop_server", ctx, server_name="SRS-1"))
    try:
        await started.wait()
        second = await call_action("stop_server", ctx, server_name="srs-1")
    finally:
        release.set()
        assert (await first).success, "the first call still runs to completion"

    assert second.success is False
    assert second.code == TARGET_BUSY
    assert second.message == "Another action on srs-1 is still running. Wait and retry.", \
        "the guard's own sentence is unchanged; only the code was added"


@pytest.mark.asyncio
async def test_an_action_that_finishes_releases_the_guard_and_the_next_call_succeeds(registry, ctx):
    """The busy code marks a REFUSAL, not a stuck target: the key is dropped on the way out."""

    async def start_server(ctx, server_name: str) -> ServerControlResult:
        return ServerControlResult(success=True, server_name=server_name)

    registry["start_server"] = start_server
    assert (await call_action("start_server", ctx, server_name="SRS-1")).success is True
    second = await call_action("start_server", ctx, server_name="SRS-1")
    assert second.success is True
    assert second.code == ""


@pytest.mark.asyncio
async def test_read_action_has_no_guard_and_never_reports_target_busy(registry, ctx):
    """A read on a busy target is served: the code is set at the write guard only."""
    ran: list[str] = []

    async def get_server_status(ctx, server_name: str) -> ServerControlResult:
        ran.append(server_name)
        return ServerControlResult(success=True, server_name=server_name)

    registry["get_server_status"] = get_server_status
    actions_module._in_flight.add("server:srs-1")
    try:
        result = await read_action("get_server_status", ctx, server_name="srs-1")
    finally:
        reset_in_flight()

    assert result.success is True
    assert result.code == ""
    assert ran == ["srs-1"], "the read ran; a read is never refused by a running write"


# ── refusal 3: the action raised ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_action_marks_a_raising_action_failed(registry, ctx):
    async def explode(ctx, server_name: str) -> ServerControlResult:
        raise RuntimeError("boom")

    registry["explode"] = explode
    result = await call_action("explode", ctx, server_name="SRS-1")

    assert result.success is False
    assert result.code == ACTION_FAILED
    assert result.message == "Action 'explode' failed: boom"


@pytest.mark.asyncio
async def test_read_action_marks_a_raising_action_failed(registry, ctx):
    async def explode(ctx, server_name: str) -> ServerControlResult:
        raise RuntimeError("boom")

    registry["explode"] = explode
    result = await read_action("explode", ctx, server_name="SRS-1")

    assert result.success is False
    assert result.code == ACTION_FAILED
    assert result.message == "Action 'explode' failed: boom"


# ── the action's own answer carries no code ───────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_action_leaves_the_code_empty(registry, ctx):
    async def start_server(ctx, server_name: str) -> ServerControlResult:
        return ServerControlResult(success=True, server_name=server_name)

    registry["start_server"] = start_server
    result = await call_action("start_server", ctx, server_name="SRS-1")

    assert result.success is True
    assert result.code == ""


@pytest.mark.asyncio
async def test_a_domain_refusal_leaves_the_code_empty(registry, ctx):
    """The action's OWN refusal is a 400, not one of the seam's three: it answers no code."""
    async def stop_server(ctx, server_name: str) -> ServerControlResult:
        return ServerControlResult(success=False, message="Server 'SRS-1' is not running.")

    registry["stop_server"] = stop_server
    for dispatch in (call_action, read_action):
        result = await dispatch("stop_server", ctx, server_name="SRS-1")
        assert result.success is False
        assert result.code == "", "a domain refusal must not be dressed as a seam refusal"
        assert result.message == "Server 'SRS-1' is not running."


@pytest.mark.asyncio
async def test_an_untyped_action_answer_carries_no_code(registry, ctx):
    """The seam's ``bool``/``str`` coercion builds a plain result — no code on it either."""
    async def returns_bool(ctx, server_name: str) -> bool:
        return False

    registry["returns_bool"] = returns_bool
    result = await call_action("returns_bool", ctx, server_name="SRS-1")

    assert result.success is False
    assert result.code == ""


# ── the envelope is untouched ─────────────────────────────────────────────

def test_to_dict_of_a_success_is_byte_identical():
    result = ActionResult(success=True, message="Started.", data={"server_name": "SRS-1"})
    assert result.to_dict() == {"status": "ok", "message": "Started.", "server_name": "SRS-1"}


def test_to_dict_of_a_failure_is_byte_identical():
    result = ActionResult(success=False, message="Not running.")
    assert result.to_dict() == {"status": "error", "message": "Not running."}


def test_to_dict_never_carries_the_code():
    """A CODED refusal renders exactly like any other failure: no key is added to the envelope."""
    coded = ActionResult(success=False, message="Wait and retry.", code=TARGET_BUSY)
    assert coded.to_dict() == {"status": "error", "message": "Wait and retry."}
    assert "code" not in coded.to_dict()

    for code in (ACTION_UNAVAILABLE, TARGET_BUSY, ACTION_FAILED):
        result = ActionResult(success=False, message="x", code=code)
        assert result.to_dict() == {"status": "error", "message": "x"}, code


def test_to_dict_of_a_subclass_is_unchanged_by_the_new_field():
    result = ServerControlResult(success=True, message="Stopped.", server_name="SRS-1",
                                 data={"audit": "recorded"})
    assert result.to_dict() == {"status": "ok", "message": "Stopped.", "audit": "recorded"}


def test_the_field_defaults_to_empty_on_every_result_class():
    """A result built anywhere else — a plugin, a test, a route — carries no code unless set."""
    assert ActionResult(success=True).code == ""
    assert ServerControlResult(success=True, server_name="SRS-1").code == ""