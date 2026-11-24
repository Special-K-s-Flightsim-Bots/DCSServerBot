"""One test per write-seam hardening finding.

Same doctrine as ``test_webui_write_actions.py``: the seam itself is driven -- a fake identity, a fake
cluster, and an EXISTING registered probe declared with ``@action`` inside the test. The findings closed
at the seam: the in-flight guard keyed the literal string while ``resolve_server`` is case-insensitive
(``SRS-1`` and ``srs-1`` ran concurrently); the audit actor accepted arbitrary text into the greppable
``[web:…]`` prefix (a ``]`` or newline could forge a second prefix or break the line); ``_target_key``
stripped but the action received the raw parameter; and nothing noticed an action that never called
``audit_action``."""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

import core.actions as actions_module
from core.action_results import ActionResult
from core.actions import (ActionContext, call_action, action_registry, action,
                          reset_in_flight)
from services.webservice.scope import Scope


@pytest.fixture
def clean_action_registry():
    """The action registry and the in-flight guard are PROCESS state — restore both after every test."""
    saved = dict(action_registry())
    reset_in_flight()
    try:
        yield
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        reset_in_flight()


def fake_cluster(servers=None):
    """``(bus, node)`` — the two things every ``ActionContext`` constructor takes."""
    bus = types.SimpleNamespace(servers=dict(servers or {}))
    node = types.SimpleNamespace(name="NODE", master=True)
    return bus, node


def fake_identity(*, roles=("DCS Admin",), scope=None, display_name="Special K",
                  backend="local", subject="specialk"):
    """A duck-typed stand-in for ``services.webservice.auth.Identity`` (its read attributes)."""
    return types.SimpleNamespace(
        roles=frozenset(roles),
        scope=Scope.everything() if scope is None else scope,
        display_name=display_name,
        shown_name=display_name,
        backend=backend,
        subject=subject,
    )


# the guard folds case

@pytest.mark.asyncio
async def test_the_in_flight_guard_folds_case_so_two_spellings_of_one_name_cannot_both_run(
        clean_action_registry):
    """``SRS-1`` and ``srs-1`` name ONE server for ``resolve_server``, so they must not overlap."""
    started = asyncio.Event()
    release = asyncio.Event()
    runs: list[str] = []

    @action
    async def case_probe(ctx, server_name):
        runs.append(server_name)
        if len(runs) > 1:
 # something else got in while the first call was still running
            return ActionResult(success=True, message=f"{server_name} ran concurrently")
        started.set()
        await release.wait()
        return ActionResult(success=True, message=f"{server_name} done")

    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    qualname = case_probe.__qualname__

    first = asyncio.create_task(call_action(qualname, ctx, server_name="SRS-1"))
    await started.wait()

    second = await call_action(qualname, ctx, server_name="srs-1")
    assert second.success is False, "a case variant of an in-flight target must be refused"
    assert "still running" in second.message
    assert runs == ["SRS-1"], "the second call must not have reached the action at all"

    release.set()
    assert (await first).success is True

 # the guard is released for the folded key too
    assert (await call_action(qualname, ctx, server_name="srs-1")).success is True


# the audit actor cannot be forged

@pytest.mark.asyncio
async def test_the_audit_actor_cannot_be_forged_by_a_hostile_subject():
    """``[web:backend/subject]`` is a greppable TOKEN: one line, one prefix, nothing else."""
    bus, node = fake_cluster()
    ctx = ActionContext.from_web(
        fake_identity(subject="evil] [web:local/admin\ninjected\rline"), node, bus)

    actor = ctx.audit_actor
    assert actor.startswith("[web:local/") and actor.endswith("]")
    assert actor.count("[web:") == 1, "the subject forged a second actor prefix"
    assert "\n" not in actor and "\r" not in actor, "the subject broke the line the actor is on"
    assert "]" not in actor[1:-1], "the subject carried a bracket into the prefix's middle"


@pytest.mark.asyncio
async def test_the_audit_backend_is_held_to_the_same_token_rule():
    """The backend is code-controlled today; the rule is one rule, applied to both halves."""
    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(backend="lo]cal\nx", subject="specialk"), node, bus)
    assert ctx.audit_actor.count("[web:") == 1
    assert "\n" not in ctx.audit_actor
    assert ctx.audit_actor.startswith("[web:") and ctx.audit_actor.endswith("]")


# one normalisation for guard and action alike

@pytest.mark.asyncio
async def test_the_action_receives_the_normalised_target_the_guard_keyed(clean_action_registry):
    """The guard strips, so the action must be handed the stripped value too — or the guard keys
    ``Real`` while the resolver is asked about ``"Real "`` and answers NOT_FOUND."""
    seen: dict[str, str] = {}

    @action
    async def capture_probe(ctx, server_name):
        seen["name"] = server_name
        return ActionResult(success=True, message=f"{server_name} done")

    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = await call_action(capture_probe.__qualname__, ctx, server_name="Real ")

    assert result.success is True
    assert seen["name"] == "Real", "the action got the raw parameter the guard had already stripped"


@pytest.mark.asyncio
async def test_a_whitespace_only_target_is_no_target_at_all(clean_action_registry):
    """The other side of the same normalisation: ``"   "`` is not a name, so the call carries no
    guard key and the action is handed the normalised (empty) value."""
    seen: dict[str, str] = {}

    @action
    async def blank_probe(ctx, server_name):
        seen["name"] = server_name
        return ActionResult(success=True, message="done")

    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    assert (await call_action(blank_probe.__qualname__, ctx, server_name="   ")).success is True
    assert seen["name"] == ""
    assert actions_module.in_flight_targets() == frozenset()


# an unaudited action warns

@pytest.mark.asyncio
async def test_an_action_that_returns_no_audit_entry_is_warned_about(clean_action_registry, caplog):
    """A forgotten trail must be VISIBLE, and the warning must never change the outcome."""
    @action
    async def unaudited_probe(ctx, server_name):
        return ActionResult(success=True, message="done")

    @action
    async def audited_probe(ctx, server_name):
        result = ActionResult(success=True, message="done")
        result.data["audit"] = actions_module.AUDIT_RECORDED
        return result

    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(), node, bus)

    with caplog.at_level(logging.WARNING, logger="core.actions"):
        result = await call_action(unaudited_probe.__qualname__, ctx, server_name="SRS-1")
    assert result.success is True, "the warning must never turn a success into a failure"
    assert "unaudited_probe" in caplog.text, "the warning must name the action that forgot"
    assert "audit" in caplog.text.lower()

 # the positive control: an action that DID record its entry is not warned about
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        await call_action(audited_probe.__qualname__, ctx, server_name="SRS-1")
    assert caplog.text == "", "an action that records its audit must not be warned about"


@pytest.mark.asyncio
async def test_the_seams_own_refusals_are_not_reported_as_unaudited_actions(
        clean_action_registry, caplog):
    """The warning is about an ACTION's outcome, not the seam's own answers.

    An absent action and an in-flight target are ``call_action``'s refusals -- no action ran, so warning
    there would be noise on every probe of an unloaded plugin.
    """
    @action
    async def silent_probe(ctx, server_name):
        return ActionResult(success=False, message=f"{server_name} refused",
                            data={"audit": actions_module.AUDIT_RECORDED})

    bus, node = fake_cluster()
    ctx = ActionContext.from_web(fake_identity(), node, bus)

    with caplog.at_level(logging.WARNING, logger="core.actions"):
        absent = await call_action("no_such_action_at_all", ctx, server_name="SRS-1")
    assert absent.success is False and "not available" in absent.message
    assert caplog.text == "", "the absent-action refusal is not an unaudited action"

    refused = await call_action(silent_probe.__qualname__, ctx, server_name="SRS-1")
    assert refused.success is False and refused.message == "SRS-1 refused"
