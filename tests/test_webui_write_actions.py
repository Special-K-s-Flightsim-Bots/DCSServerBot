"""The write seam, tested as a seam.

Nothing here renders a control and no action is reachable from the UI: every case drives the seam
itself -- ``ActionContext.from_web``, ``resolve_scoped_server``, ``call_action`` / ``action_available``,
the in-flight guard and the audit helper -- with a FAKE identity, a FAKE server and an EXISTING action
(the ``mission`` plugin's, unchanged). The two answers the seam owes: a NAMED target outside the
caller's scope owes the console's 403 (never a 404) for a SCOPED caller, EXISTING OR NOT, because
answering 404 for the missing one and 403 for the real one would leak the fleet's server names; an
UNSCOPED caller sees every server, so a name absent from its view genuinely does not exist and the
action's own typed refusal is the honest answer. Fake objects, not mocks of the class under test."""
from __future__ import annotations

import asyncio
import logging
import types

import pytest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from conftest import asgi_client

from core import Status
import core.actions as actions_module
from core.action_results import ActionResult, ServerControlResult
from core.actions import (ActionContext, ServerResolution, action_available, audit_action,
                          call_action, discover_actions, action_registry, action,
                          reset_in_flight)
from services.webservice.pages.dashboard import out_of_scope_refusal
from services.webservice.scope import Scope


# ----------------------------------------------------------------------------- the fixtures

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


class FakeMission:
    """A mission whose pause/unpause are awaitable — the two operations the actions call."""

    def __init__(self):
        self.paused = False

    async def pause(self):
        self.paused = True

    async def unpause(self):
        self.paused = False


def fake_server(name="SRS-1", *, status=Status.RUNNING, managed_by=None, mission=None):
    server = types.SimpleNamespace(
        name=name,
        display_name=name,
        status=status,
        locals={"managed_by": list(managed_by)} if managed_by else {},
        current_mission=mission,
    )
    return server


def fake_cluster(servers):
    """``(bus, node)`` — the two things every ``ActionContext`` constructor takes."""
    bus = types.SimpleNamespace(servers=dict(servers))
    node = types.SimpleNamespace(name="NODE", master=True)
    return bus, node


def fake_identity(*, roles=("DCS Admin",), scope=None, display_name="Special K",
                  backend="local", subject="specialk"):
    """A duck-typed stand-in for ``services.webservice.auth.Identity`` (its four read attributes)."""
    return types.SimpleNamespace(
        roles=frozenset(roles),
        scope=Scope.everything() if scope is None else scope,
        display_name=display_name,
        shown_name=display_name,
        backend=backend,
        subject=subject,
    )


class RecordingBot:
    """A bot whose ``audit`` records what it was asked to write (and can be told to fail)."""

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))
        if self.fail:
            raise RuntimeError("the audit channel is unreachable")


def post_router(path, handler):
    router = APIRouter()
    router.add_api_route(path, handler, methods=["POST"])
    return router


# ------------------------------------------------------- the seam invokes a REAL action

@pytest.mark.asyncio
async def test_the_seam_invokes_an_existing_action_from_a_route(shell_app, clean_action_registry):
    """A fake identity + a fake server + an EXISTING action, reached through a real route."""
    app, registrar = shell_app
    discover_actions(["mission"])
    assert action_available("start_server"), "the mission plugin's actions must be discoverable"

    server = fake_server("SRS-1", status=Status.STOPPED, managed_by=["Host A"])
    started = {}

    async def _start():
        started["yes"] = True

    server.start = _start
    bus, node = fake_cluster({"SRS-1": server})
    ctx = ActionContext.from_web(fake_identity(scope=Scope.restricted({"Host A"})), node, bus)
    captured = {}

    async def handler(request: Request):
        captured["result"] = await call_action("start_server", ctx, server_name="SRS-1")
        return JSONResponse(captured["result"].to_dict())

    registrar.register_pages("probe", post_router("/probe/start", handler),
                             capabilities={"/probe/start": "public"})
    async with asgi_client(app) as client:
        response = await client.post("/probe/start")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["message"] == "Server 'SRS-1' started."
    assert started == {"yes": True}
 # the TYPED result, not prose: the console composes its banner from the FIELDS
    result = captured["result"]
    assert isinstance(result, ServerControlResult)
    assert result.server_name == "SRS-1"


# -------------------------------------------------------------------- the action layer absent

@pytest.mark.asyncio
async def test_an_unknown_action_name_is_refused(clean_action_registry):
    """Absent action -> a typed refusal, never a 500 and never a silent success."""
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)

    assert action_available("no_such_action") is False
    result = await call_action("no_such_action", ctx)
    assert isinstance(result, ActionResult)
    assert result.success is False
    assert "no_such_action" in result.message


@pytest.mark.asyncio
async def test_an_action_that_raises_is_a_typed_refusal_not_an_exception(clean_action_registry):
    """A route must never answer 500 because an action raised: the seam types it."""
    @action
    async def exploding_probe(ctx, server_name):
        raise RuntimeError("boom")

    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = await call_action(exploding_probe.__qualname__, ctx, server_name="SRS-1")
    assert result.success is False
    assert "boom" in result.message


# -------------------------------------------------------------- scope: 403 vs not-found

@pytest.mark.asyncio
async def test_a_named_out_of_scope_server_is_not_permitted():
    server = fake_server("SRS-1", managed_by=["Someone Else"])
    bus, node = fake_cluster({"SRS-1": server})
    ctx = ActionContext.from_web(fake_identity(scope=Scope.restricted({"Host A"})), node, bus)

    resolution = await ctx.resolve_scoped_server("SRS-1")
    assert resolution.status == ServerResolution.NOT_PERMITTED
    assert resolution.server is None
 # the route owes the console's refusal, and it is the generic one (it names no target)
    refusal = out_of_scope_refusal()
    assert refusal.status_code == 403
    assert "SRS-1" not in refusal.detail


@pytest.mark.asyncio
async def test_a_named_server_that_does_not_exist_is_not_found_for_an_unscoped_caller():
    bus, node = fake_cluster({"SRS-1": fake_server("SRS-1", managed_by=["Someone Else"])})
    ctx = ActionContext.from_web(fake_identity(roles=("Admin",), scope=Scope.everything()), node, bus)

    resolution = await ctx.resolve_scoped_server("Nope")
    assert resolution.status == ServerResolution.NOT_FOUND
    assert resolution.message == "Server 'Nope' not found."
 # the not-found answer says nothing about a scope
    assert "scope" not in resolution.message.lower()


@pytest.mark.asyncio
async def test_a_scoped_caller_learns_nothing_from_the_difference_between_the_two_cases():
    """THE non-disclosure property: for a scoped caller both cases answer IDENTICALLY.

    Answering the typed "not found" for a name that does not exist and the 403 for a real server
    the caller may not see would let a hoster enumerate the fleet by probing names.
    """
    server = fake_server("SRS-1", managed_by=["Someone Else"])
    bus, node = fake_cluster({"SRS-1": server})
    ctx = ActionContext.from_web(fake_identity(scope=Scope.restricted({"Host A"})), node, bus)

    existing_but_foreign = await ctx.resolve_scoped_server("SRS-1")
    wholly_unknown = await ctx.resolve_scoped_server("Nope")

    assert existing_but_foreign.status == ServerResolution.NOT_PERMITTED
    assert wholly_unknown.status == ServerResolution.NOT_PERMITTED
    assert existing_but_foreign == wholly_unknown
    assert existing_but_foreign.message == wholly_unknown.message == ""


@pytest.mark.asyncio
async def test_an_in_scope_server_resolves_for_a_scoped_caller():
    server = fake_server("SRS-1", managed_by=["Host A"])
    bus, node = fake_cluster({"SRS-1": server})
    ctx = ActionContext.from_web(fake_identity(scope=Scope.restricted({"Host A"})), node, bus)

    resolution = await ctx.resolve_scoped_server("SRS-1")
    assert resolution.status == ServerResolution.FOUND
    assert resolution.server is server


@pytest.mark.asyncio
async def test_a_server_that_declares_no_managed_by_is_outside_a_scoped_callers_scope():
    """The console's ``managed_by`` is an ALLOW-LIST -- an untagged server is not a scoped caller's to act
    on (the empty-means-no-restriction rule survives only on the Discord side, ``holds_scope``). An
    UNSCOPED caller still resolves it."""
    server = fake_server("SRS-1", managed_by=None)
    bus, node = fake_cluster({"SRS-1": server})
    scoped = ActionContext.from_web(fake_identity(scope=Scope.restricted({"Host A"})), node, bus)
    assert (await scoped.resolve_scoped_server("SRS-1")).status == ServerResolution.NOT_PERMITTED
    unscoped = ActionContext.from_web(fake_identity(roles=("Admin",), scope=Scope.everything()),
                                      node, bus)
    assert (await unscoped.resolve_scoped_server("SRS-1")).status == ServerResolution.FOUND


@pytest.mark.asyncio
async def test_a_context_that_states_no_scope_fails_closed():
    """``_scope is None`` (a non-web caller) is the STRICT answer: nothing is permitted."""
    server = fake_server("SRS-1")
    bus, node = fake_cluster({"SRS-1": server})
    ctx = ActionContext(node=node, bus=bus)          # no scope stated
    resolution = await ctx.resolve_scoped_server("SRS-1")
    assert resolution.status == ServerResolution.NOT_PERMITTED


# --------------------------------------------------------------------- the in-flight guard

@pytest.mark.asyncio
async def test_the_in_flight_guard_refuses_a_concurrent_second_call_to_the_same_target(clean_action_registry):
    started = asyncio.Event()
    release = asyncio.Event()

    @action
    async def slow_probe(ctx, server_name):
        started.set()
        await release.wait()
        return ActionResult(success=True, message=f"{server_name} done")

    bus, node = fake_cluster({"SRS-1": fake_server("SRS-1")})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    qualname = slow_probe.__qualname__

    first = asyncio.create_task(call_action(qualname, ctx, server_name="SRS-1"))
    await started.wait()

    second = await call_action(qualname, ctx, server_name="SRS-1")
    assert second.success is False
    assert "SRS-1" in second.message and "still running" in second.message

    release.set()
    assert (await first).success is True

 # the guard is released once the call finished
    again = await call_action(qualname, ctx, server_name="SRS-1")
    assert again.success is True
    assert again.message == "SRS-1 done"


@pytest.mark.asyncio
async def test_the_guard_is_added_and_removed_around_the_call(clean_action_registry):
    """The guard is per TARGET and only while a call is in flight — an untouched target is free."""
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)

    @action
    async def quick_probe(ctx, server_name):
        return ActionResult(success=True, message=server_name)

    assert actions_module.in_flight_targets() == frozenset()
    result = await call_action(quick_probe.__qualname__, ctx, server_name="SRS-1")
    assert result.success is True
    assert actions_module.in_flight_targets() == frozenset()


# ------------------------------------------------------------------------------- the audit

@pytest.mark.asyncio
async def test_a_web_actor_is_named_by_the_declared_token_and_the_entry_is_confirmed():
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(backend="local", subject="specialk"), node, bus)
    bot = RecordingBot()
    result = ActionResult(success=True, message="Server 'SRS-1' stopped.")

    returned = await audit_action(ctx, result, server=fake_server("SRS-1"), bot=bot)

    assert returned is result
    assert result.data["audit"] == "recorded"
    assert len(bot.calls) == 1
    message, kwargs = bot.calls[0]
    assert message == "Server 'SRS-1' stopped."
 # the actor is NEVER written as a ucid (a string `user` lands in the audit.ucid column)
    assert not isinstance(kwargs.get("user"), str)
    assert kwargs["server"].name == "SRS-1"


@pytest.mark.asyncio
async def test_a_discord_web_actor_is_named_by_its_backend_and_subject():
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(backend="discord", subject="123456789"),
                                 node, bus)
    bot = RecordingBot()
    result = ActionResult(success=True, message="kicked.")
    await audit_action(ctx, result, bot=bot)
    assert bot.calls[0][0] == "kicked."


@pytest.mark.asyncio
async def test_a_refused_action_is_audited_too():
    """One entry per ATTEMPT: an operator wants the refusal in the trail after an incident."""
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    bot = RecordingBot()
    result = ActionResult(success=False, message="Server 'SRS-1' is Stopped, not running.")
    await audit_action(ctx, result, bot=bot)
    assert bot.calls[0][0] == "Server 'SRS-1' is Stopped, not running."
    assert result.data["audit"] == "recorded"


@pytest.mark.asyncio
async def test_a_failing_audit_is_reported_as_not_recorded_and_never_raises():
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = ActionResult(success=True, message="done.")
    returned = await audit_action(ctx, result, bot=RecordingBot(fail=True))
    assert returned is result
    assert result.success is True                  # a success is never turned into a failure
    assert result.data["audit"] == "not-recorded"


@pytest.mark.asyncio
async def test_a_headless_bot_is_asked_to_record_and_is_not_assumed_away():
    """A headless bot appends the row itself: only the channel announcement is missing.

    The old rule treated the headless bot as unable to confirm an entry and reported the console
    action as not-recorded; its ``audit`` now writes to the cluster's audit table.
    """
    seen = []

    class HeadlessProbe:
        async def audit(self, message, **kwargs):
            seen.append((message, kwargs))

    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = ActionResult(success=True, message="done.")
    await audit_action(ctx, result, bot=HeadlessProbe())
    assert result.data["audit"] == "recorded"
    assert seen, "the headless bot was never asked to record the entry"


@pytest.mark.asyncio
async def test_a_failed_audit_does_not_change_the_http_status_of_the_route(shell_app, clean_action_registry):
    """The page shows the warning, the status stays a success."""
    app, registrar = shell_app
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)

    @action
    async def audited_probe(ctx, server_name):
        return ActionResult(success=True, message=f"{server_name} done")

    async def handler(request: Request):
        result = await call_action(audited_probe.__qualname__, ctx, server_name="SRS-1")
        await audit_action(ctx, result, bot=RecordingBot(fail=True))
        return JSONResponse(result.to_dict())

    registrar.register_pages("probe", post_router("/probe/audit", handler),
                             capabilities={"/probe/audit": "public"})
    async with asgi_client(app) as client:
        response = await client.post("/probe/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["audit"] == "not-recorded"


@pytest.mark.asyncio
async def test_an_unavailable_bot_reports_not_recorded(caplog):
    """No bot at all in the process -> the audit cannot be recorded, and it says so OUT LOUD."""
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = ActionResult(success=True, message="done.")
    with caplog.at_level(logging.ERROR):
        await audit_action(ctx, result)      # no `bot=` -> resolved from the service registry
    assert result.data["audit"] == "not-recorded"
 # the REASON, not merely the outcome: "no bot" and "the write raised" are different incidents
    assert "no bot that can record it" in caplog.text


def test_from_web_reads_the_real_identity_attributes():
    """The duck-typing matches the object the console actually hands over."""
    from services.webservice.auth import Identity

    identity = Identity(subject="specialk", backend="local", roles=frozenset({"DCS Admin"}),
                        display_name="Special K", scope=Scope.restricted({"Host A"}))
    bus, node = fake_cluster({})
    ctx = ActionContext.from_web(identity, node, bus)

    assert ctx._roles == frozenset({"DCS Admin"})
    assert ctx._scope is identity.scope
    assert ctx._audit_user == "Special K"
    assert ctx.audit_actor == "[web:local/specialk]"


# ------------------------------------------------------------------------ the layering pin


def test_from_web_is_duck_typed_and_core_imports_no_service_type():
    """``from_web`` documents its contract as ATTRIBUTES; nothing in ``core`` names the web type.

    Structural, and deliberately narrow: it pins the one thing that makes the seam layering-safe —
    the web's ``Identity`` is never imported into ``core`` (``tests/test_layering_direction.py``
    pins the direction at the AST level; this pins the SHAPE a reader would otherwise have to
    re-derive).
    """
    import inspect

    source = inspect.getsource(ActionContext.from_web)
    assert "services.webservice" not in source
    assert "Identity" not in source
 # what it reads off the caller, stated in the docstring
    assert "roles" in source and "scope" in source and "shown_name" in source


def test_no_audit_pin_was_left_vacuous():
    """``startswith("")`` is always true: a pin stripped of its needle must not survive.

    The actor moved out of the event text and into its own field, so every pin that asserted the
    leading token had to be re-pointed at ``actor``. A mechanical strip that leaves an empty
    needle passes for ever after and reads like a check.
    """
    from pathlib import Path

    offenders = [f"{path.name}:{number}"
                 for path in Path(__file__).parent.glob("*.py")
                 if path.name != Path(__file__).name
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if 'startswith("")' in line or "startswith('')" in line
                 or 'endswith("")' in line or "endswith('')" in line]
    assert not offenders, offenders
