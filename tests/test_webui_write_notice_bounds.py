"""The one-shot notice and the audit event are BOUNDED.

``_remember_notice`` wrote ``result.message`` VERBATIM into ``request.session``, and Starlette's
``SessionMiddleware`` keeps the session IN the signed cookie -- so a crafted POST naming a
200 000-character server made the NOT_FOUND refusal quote it whole and the response carried a
267,123-byte ``Set-Cookie`` (a self-inflicted logout per crafted POST, and a 400/431 at any reverse
proxy with a small header limit). The same unbounded string reached ``audit_action``'s event text.
These tests pin the two bounds where they were added -- the session and the audit seam -- and the
behaviour that the notice survives exactly one redirect, then is gone, asserting on the REAL
``Set-Cookie`` header through the app rather than on the helper in isolation."""
from __future__ import annotations

import types

import pytest

import core.actions as actions_module
from core.action_results import ActionResult
from core.actions import AUDIT_RECORDED, ActionContext, audit_action
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from services.webservice.scope import Scope

from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    BOT_YAML, COOKIE_NAME, build, clean_registry, client, config_dir, fleet, recording_bot,
    sign_in, token_of,
)

#: a crafted name of 200 000 characters, whose refusal is the reproduction
HUGE = "X" * 200_000


def session_cookie_value(response) -> str:
    """The SESSION cookie's value out of the real ``Set-Cookie`` headers of *response*.

    Read off the wire, not off ``request.session``: the defect was a cookie too big for a browser,
    which no in-process assertion about the dict could have caught.
    """
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{COOKIE_NAME}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"no {COOKIE_NAME} cookie in {response.headers.get_list('set-cookie')!r}")


def fake_identity(**overrides):
    """A duck-typed stand-in for the console's ``Identity`` (its read attributes only)."""
    fields = {"roles": frozenset({"Admin"}), "scope": Scope.everything(),
              "display_name": "Special K", "shown_name": "Special K",
              "backend": "local", "subject": "specialk"}
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


# 1. the session cookie is bounded

@pytest.mark.asyncio
async def test_a_huge_refusal_notice_keeps_the_session_cookie_under_2kb(config_dir, clean_registry,
                                                                       recording_bot):
    """A 200 000-character server name leaves the ``Set-Cookie`` value small.

    Before the bound the value was 267,055 bytes; the stored message is now clipped to
    ``NOTICE_MAX_CHARS`` and the clip is VISIBLE, so the operator sees a refusal marked as clipped
    instead of a cookie no browser will store.
    """
    cluster = fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "specialk")               # Admin: unscoped, so the name is NOT_FOUND
        page = await http.get(servers_page.SERVERS_PATH)
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": HUGE, "_csrf_token": token_of(page.text)})
        assert reply.status_code == 303, reply.text
        value = session_cookie_value(reply)
        assert len(value.encode("utf-8")) < 2048, \
            f"the session cookie carried {len(value.encode('utf-8'))} bytes"
        after = (await http.get(servers_page.SERVERS_PATH)).text

 # what the OPERATOR reads: a REFUSAL banner (bad), naming the attempt, marked as clipped — the
 # the clip cuts the tail of the 200 000-char name
    assert 'class="notice bad"' in after, "the clipped outcome must still render as a refusal"
    assert ("Server 'X" in after) or ("Server &#39;X" in after), \
        "the notice must still name the attempt it was about"
    assert actions_module.TRUNCATION_MARKER.strip() in after, \
        "a clipped refusal must be visible as clipped, not silently shortened"

 # the SAME attempt is bounded in the audit trail, and still names the actor and the attempt
    assert recording_bot.calls, "the attempt left no audit entry"
    event = recording_bot.calls[0][0]
    assert len(event) <= actions_module.AUDIT_EVENT_MAX_CHARS, f"audit event was {len(event)} chars"
    assert event.startswith("Server 'X"), "the attempt must still be named"
    assert event.endswith(actions_module.TRUNCATION_MARKER)


# --------------------------------------------------------- 2. the seam is the bound (core/actions.py)

@pytest.mark.asyncio
async def test_the_audit_seam_bounds_a_huge_event_so_every_action_inherits_it():
    """The bound lives in ``audit_action`` — NOT per route — so a future action inherits it.

    Driven at the seam (no app, no session): only the audit text is under test here, and the actor
    prefix leads the text so the tail clip never loses WHO made the attempt.
    """
    written: list[str] = []

    class Bot:
        async def audit(self, message, **kwargs):
            written.append(message)

    bus = types.SimpleNamespace(servers={})
    node = types.SimpleNamespace(name="NODE", master=True)
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = ActionResult(success=False, message=f"Server '{HUGE}' not found.")

    returned = await audit_action(ctx, result, bot=Bot())

    assert returned.data["audit"] == AUDIT_RECORDED
    assert len(written) == 1
    assert len(written[0]) <= actions_module.AUDIT_EVENT_MAX_CHARS
    assert written[0].startswith("Server 'X")
    assert written[0].endswith(actions_module.TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_an_ordinary_audit_event_is_untouched_by_the_bound():
    """The positive control: the bound is a ceiling, so a normal refusal is written whole."""
    written: list[str] = []

    class Bot:
        async def audit(self, message, **kwargs):
            written.append(message)

    bus = types.SimpleNamespace(servers={})
    node = types.SimpleNamespace(name="NODE", master=True)
    ctx = ActionContext.from_web(fake_identity(), node, bus)
    result = ActionResult(success=False, message="Server 'GHOST-9' not found.")

    await audit_action(ctx, result, bot=Bot())

    assert written == ["Server 'GHOST-9' not found."]


# 3. the one-shot behaviour

@pytest.mark.asyncio
async def test_the_notice_survives_exactly_one_redirect_and_then_is_gone(config_dir, clean_registry,
                                                                         recording_bot):
    """The bound must not change the ONE-SHOT contract: shown once, dropped after."""
    cluster = fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        html = (await http.get(servers_page.SERVERS_PATH)).text
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": "SRS-1", "_csrf_token": token_of(html)})
        assert reply.status_code == 303
        first = (await http.get(servers_page.SERVERS_PATH)).text
        second = (await http.get(servers_page.SERVERS_PATH)).text

    assert 'class="notice' in first, "the redirect's notice was not rendered"
    assert "paused" in first.lower()
    assert 'class="notice' not in second, "the notice repeated on a second read — it is not one-shot"
