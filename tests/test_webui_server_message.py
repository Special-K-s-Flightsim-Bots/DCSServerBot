"""The SERVER row's message pair: chat and popup, each a glyph with its own dialog and an audience.

One section per acceptance criterion: the DISCOVERY WIRING (``action_available("message_server")``
must be true through the web shell's own install, or every control is silently omitted while the
suite stays green); the ACTION (an empty message is refused WITHOUT touching the domain; both modes
reach the right method with the right ``Coalition`` — a chat line to a coalition is delivered by the
domain's own FAN-OUT, so both modes offer the same three recipients; the recipient allow-list is the
ACTION's own, so a crafted value cannot steer the write; a coalition needs no server-side
configuration); the STRIP (two glyphs on a server row, each posting to its dialog); the DIALOG (the
message field and the recipient SELECT — all three recipients on every server, whether or not its
config declares coalitions); the ROUND TRIP; the CRAFTED POST (refused AND audited); the OMITTED
controls (no capability, no plugin); the COPY (no sentence claims DCS has no coalition chat, in the
console or in a catalog); and the deploy pins, including the DOMAIN fan-out the third recipient
rests on.

Driven through the real app and the stub cluster; the domain boundary is the server double.
"""
from __future__ import annotations

import inspect
import re
import types
from pathlib import Path

import pytest

from core import Coalition, Status
from core.action_results import ServerControlResult
from core.actions import ActionContext, action_available, action_registry
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page

# the console write suites' own harness and server double, reused rather than copied
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

SERVER = "SRS-1"
SENDER = "mod1"
#: the tree's root, read from this file — never a machine path
REPO = Path(__file__).resolve().parent.parent

SERVER_CHAT = actions_page.SERVER_CHAT_PATH
SERVER_POPUP = actions_page.SERVER_POPUP_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX


class MessageServer(FakeServer):
    """The ``Server`` double: the two server-wide message methods, recording what they were given.

    The recorded tuple carries the ``Coalition`` ITSELF (not its value), so "each recipient reaches
    the domain call as the right ``Coalition``" is an identity assertion rather than a string compare.
    ``coalitions`` reproduces the DOMAIN's own test — ``Server.coalitions`` is
    ``locals['coalitions'] is not None`` (``core/data/server.py``) — so a test cannot pass against a
    double that answers the question differently from the server it stands in for.
    """

    def __init__(self, name, managed_by=None, *, status=Status.RUNNING, coalitions=False):
        super().__init__(name, status=status, managed_by=managed_by)
        if coalitions:
            self.locals["coalitions"] = {"lock_time": "1 day"}

    @property
    def coalitions(self) -> bool:
        """The domain's own "this server has coalitions configured"."""
        return self.locals.get("coalitions") is not None

    async def sendChatMessage(self, coalition, message, sender=None):
        self.calls.append(("chat", coalition, message, sender))

    async def sendPopupMessage(self, recipient, message, timeout=-1, sender=None):
        self.calls.append(("popup", recipient, message, timeout, sender))


def message_fleet(*, coalitions=False, status=Status.RUNNING):
    """A (Host A) and B (Host B), each with the message methods — the scope's two sides."""
    a = MessageServer("SRS-1", ["Host A"], status=status, coalitions=coalitions)
    b = MessageServer("SRS-B", ["Host B"], status=status, coalitions=coalitions)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


def direct_ctx(servers):
    """An ``ActionContext`` with no scope stated: the ACTION is driven directly (as MCP would)."""
    return ActionContext(node=types.SimpleNamespace(name="NODE"),
                         bus=types.SimpleNamespace(servers=dict(servers)))


def forms_for(html: str, path: str) -> list[str]:
    """Every rendered form whose ``action`` is *path*."""
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


def select_options(html: str) -> list[tuple[str, str]]:
    """Every ``(value, label)`` the dialog's audience SELECT offers, in declaration order."""
    match = re.search(r'<select[^>]*name="coalition"[^>]*>(.*?)</select>', html, re.S)
    assert match, f"the dialog rendered no audience select:\n{html}"
    return re.findall(r'<option value="([^"]*)">([^<]*)</option>', match.group(1))


def confirm_of(dialog: str) -> str:
    """The one-shot token the rendered dialog's form carries."""
    match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog)
    assert match, "the dialog rendered no confirm token"
    return match.group(1)


def notified(html: str, sentence: str) -> bool:
    """Whether the page's notice carries *sentence* — Jinja escapes it, so both spellings count."""
    return sentence in html or sentence.replace("'", "&#39;") in html


async def open_dialog(http, path: str, token: str, server: str = SERVER) -> str:
    """Press one message glyph: the row posts to its DIALOG, which asks for the message."""
    reply = await http.post(path + CONFIRM, data={"server": server, "_csrf_token": token})
    assert reply.status_code == 200, reply.text
    return reply.text


async def send(http, path: str, token: str, message: str, *, coalition: str = "all",
               server: str = SERVER):
    """The WHOLE round trip a glyph press is: open the dialog, then post its form."""
    dialog = await open_dialog(http, path, token, server)
    return await http.post(path, data={"server": server, "message": message,
                                       "coalition": coalition, "_csrf_token": token,
                                       "_confirm_token": confirm_of(dialog)})


# ---------------------------------------------------------------------- 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_server_message_action_in_its_own_process(
        config_dir, clean_registry):
    """``action_available("message_server")`` through the shell's OWN install.

    The console renders a control from ``action_available``; without the shell's discovery wiring the
    registry stays empty and every control is silently omitted while the suite stays green.
    """
    assert action_available("message_server") is False, "the registry must start EMPTY here"
    build(config_dir, message_fleet())
    assert action_available("message_server") is True
    assert "message_server" in action_registry()


# ------------------------------------------------------------------------ 2. the action itself

@pytest.mark.asyncio
async def test_the_action_refuses_an_empty_or_whitespace_message_without_touching_dcs():
    """ACCEPTANCE — a blank message is a TYPED refusal and the domain call does NOT happen."""
    from plugins.mission.actions import message_server

    srv = MessageServer("SRS-1")
    ctx = direct_ctx({"SRS-1": srv})

    for blank in ("", "   ", "\t", "\n  \t\n"):
        result = await message_server(ctx, "SRS-1", blank)
        assert isinstance(result, ServerControlResult)
        assert result.success is False, f"{blank!r} was accepted"
        assert result.message == "Message must not be empty."
        assert srv.calls == [], f"{blank!r} reached the domain method"

    # the positive control: a message with content DOES reach it, so the assertions above measure
    # the validation rather than a double that never records anything
    assert (await message_server(ctx, "SRS-1", "hello")).success is True
    assert srv.calls == [("popup", Coalition.ALL, "hello", -1, None)]


@pytest.mark.asyncio
async def test_the_action_refuses_an_over_long_message_and_an_unknown_mode():
    """The same validation shape for the other two refusals, neither of which sends anything."""
    from plugins.mission.actions import MAX_MESSAGE_LENGTH, message_server

    srv = MessageServer("SRS-1")
    ctx = direct_ctx({"SRS-1": srv})

    too_long = await message_server(ctx, "SRS-1", "x" * (MAX_MESSAGE_LENGTH + 1))
    assert too_long.success is False
    assert str(MAX_MESSAGE_LENGTH) in too_long.message
    assert srv.calls == []

    unknown = await message_server(ctx, "SRS-1", "hello", "shout")
    assert unknown.success is False
    assert "shout" in unknown.message and "popup" in unknown.message
    assert srv.calls == []

    # the boundary itself is ACCEPTED — the limit is a maximum, not an exclusive bound
    at_limit = await message_server(ctx, "SRS-1", "x" * MAX_MESSAGE_LENGTH)
    assert at_limit.success is True
    assert srv.calls == [("popup", Coalition.ALL, "x" * MAX_MESSAGE_LENGTH, -1, None)]


@pytest.mark.asyncio
async def test_each_recipient_reaches_the_domain_call_as_the_right_coalition():
    """ACCEPTANCE — the three words map to ``Coalition.ALL`` / ``RED`` / ``BLUE``, in BOTH modes."""
    from plugins.mission.actions import message_server

    for word, expected in (("all", Coalition.ALL), ("red", Coalition.RED),
                           ("blue", Coalition.BLUE)):
        srv = MessageServer("SRS-1", coalitions=True)
        ctx = direct_ctx({"SRS-1": srv})
        result = await message_server(ctx, "SRS-1", "Check your six.", "popup", word, SENDER)
        assert result.success is True, (word, result.message)
        assert isinstance(result, ServerControlResult)
        assert result.server_name == "SRS-1"
        assert srv.calls == [("popup", expected, "Check your six.", -1, SENDER)], word

    # the chat line reaches the chat method with the SAME coalition — the domain fans a coalition
    # out to that side's players (``core/data/server.py``), so the choice is deliverable here too
    for word, expected in (("all", Coalition.ALL), ("red", Coalition.RED),
                           ("blue", Coalition.BLUE)):
        srv = MessageServer("SRS-1", coalitions=True)
        chat = await message_server(direct_ctx({"SRS-1": srv}), "SRS-1", "Look alive.", "chat", word,
                                    SENDER)
        assert chat.success is True, (word, chat.message)
        assert srv.calls == [("chat", expected, "Look alive.", SENDER)], word


@pytest.mark.asyncio
async def test_a_crafted_coalition_cannot_steer_the_write():
    """ACCEPTANCE — a value outside the mode's allow-list is a typed refusal, and nothing is sent.

    ``neutral`` is a real ``Coalition`` (``core/data/const.py``) that this action deliberately does
    not offer; ``green`` and ``1`` are not recipient words at all. None may reach the domain — in
    either mode.
    """
    from plugins.mission.actions import message_server

    srv = MessageServer("SRS-1", coalitions=True)
    ctx = direct_ctx({"SRS-1": srv})

    for mode in ("popup", "chat"):
        for bad in ("neutral", "green", "1", "all,red"):
            result = await message_server(ctx, "SRS-1", "hi", mode, bad)
            assert result.success is False, (mode, bad)
            assert bad in result.message, (mode, bad, result.message)
            assert "Unknown recipient" in result.message, (mode, bad, result.message)
            assert srv.calls == [], (mode, bad)


@pytest.mark.asyncio
async def test_a_coalition_reaches_a_server_whose_config_declares_none():
    """A coalition recipient needs NO server-side configuration.

    ``Server.coalitions`` describes the bot's Discord coalition ROLES (``blue_role`` / ``red_role`` /
    ``lock_time`` in ``servers.yaml``) and says nothing about whether a message can be addressed to
    Red or Blue: the popup calls ``trigger.action.outTextForCoalition`` and the chat line fans out per
    player of that side, both of which work on any running mission. A server whose config carries no
    such block therefore still receives a coalition message.
    """
    from plugins.mission.actions import message_server

    srv = MessageServer("SRS-1", coalitions=False)
    ctx = direct_ctx({"SRS-1": srv})

    for word in ("red", "blue"):
        result = await message_server(ctx, "SRS-1", "hi", "popup", word)
        assert result.success is True, (word, result.message)

    assert srv.calls == [("popup", Coalition.RED, "hi", -1, None),
                         ("popup", Coalition.BLUE, "hi", -1, None)]


@pytest.mark.asyncio
async def test_a_server_that_does_not_exist_is_a_typed_refusal():
    """The seam's own resolution is the guard; nothing is sent and nothing raises."""
    from plugins.mission.actions import message_server

    result = await message_server(direct_ctx({}), "NOPE-9", "hi")
    assert result.success is False
    assert result.message == "Server 'NOPE-9' not found."


# ------------------------------------------------------------------- 3. the strip and the dialogs

@pytest.mark.asyncio
async def test_the_strip_offers_both_glyphs_on_a_server_row(config_dir, clean_registry):
    """ACCEPTANCE — ``chat`` and ``popup`` are two controls of the one ``message_server`` write, each
    a glyph with a native ``title`` AND an ``aria-label``, on BOTH screens that render the strip."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        servers = await open_page(http, servers_page.SERVERS_PATH)
        dashboard = await open_page(http, "/")
    for html in (servers, dashboard):
        assert forms_for(html, SERVER_CHAT + CONFIRM), "the chat control posts to its dialog"
        assert forms_for(html, SERVER_POPUP + CONFIRM), "the popup control posts to its dialog"
        assert 'title="Chat — a chat line in game, to the recipients you choose"' in html
        assert 'aria-label="Send a chat line on SRS-1"' in html
        assert 'title="Popup — a message box on every screen"' in html
        assert 'aria-label="Send a popup to SRS-1"' in html


@pytest.mark.asyncio
async def test_the_popup_dialog_asks_for_the_message_and_the_three_recipients(config_dir,
                                                                             clean_registry):
    """ACCEPTANCE — the message field AND the three recipients, whose values are exactly the
    action's own words and whose labels are the three the card names."""
    app = build(config_dir, message_fleet(coalitions=True))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        dialog = await open_dialog(http, SERVER_POPUP, token)

    assert 'name="message"' in dialog, "the dialog asks for the message"
    assert f'maxlength="{actions_page.MESSAGE_FIELD_MAX}"' in dialog
    assert select_options(dialog) == [("all", "Everyone"), ("red", "Red coalition"),
                                      ("blue", "Blue coalition")]


@pytest.mark.asyncio
async def test_the_chat_dialog_asks_for_the_message_and_the_same_three_recipients(config_dir,
                                                                                  clean_registry):
    """ACCEPTANCE — the chat line is addressable to a coalition (the domain fans it out), so its
    dialog offers the SAME three recipients as the popup's: one vocabulary for one choice."""
    app = build(config_dir, message_fleet(coalitions=True))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        dialog = await open_dialog(http, SERVER_CHAT, token)

    assert 'name="message"' in dialog
    assert f'maxlength="{actions_page.MESSAGE_FIELD_MAX}"' in dialog
    assert select_options(dialog) == [("all", "Everyone"), ("red", "Red coalition"),
                                      ("blue", "Blue coalition")]
    assert actions_page.SERVER_MESSAGE_AUDIENCE == tuple(
        option for action in actions_page.SERVER_ACTIONS if action.key == "popup"
        for option in action.select.options), "the two dialogs read two audiences"


@pytest.mark.asyncio
async def test_the_three_recipients_are_offered_on_a_server_that_declares_no_coalitions(
        config_dir, clean_registry):
    """The audience is a CHOICE, never a gate: all three recipients are offered on every server whose
    process can take the message, whether or not its config carries a ``coalitions:`` block.

    Both modes, because the offer is the same declaration on the same server row.
    """
    app = build(config_dir, message_fleet(coalitions=False))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        popup = await open_dialog(http, SERVER_POPUP, token)
        chat = await open_dialog(http, SERVER_CHAT, token)

    for dialog in (popup, chat):
        assert select_options(dialog) == [("all", "Everyone"), ("red", "Red coalition"),
                                          ("blue", "Blue coalition")]


# ---------------------------------------------------------------------------- 4. the round trip

@pytest.mark.asyncio
async def test_each_recipient_reaches_the_domain_call_through_the_dialog(config_dir, clean_registry,
                                                                        recording_bot):
    """ACCEPTANCE — glyph, dialog, POST, domain call, notice, audit: the whole path, per recipient."""
    cluster = message_fleet(coalitions=True)
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))

        # the notice is ONE-SHOT (each write replaces it), so the page is re-read between writes
        notices: list[str] = []
        for word, message in (("all", "One."), ("red", "Two."), ("blue", "Three.")):
            reply = await send(http, SERVER_POPUP, token, message, coalition=word)
            assert reply.status_code == 303, (word, reply.text)
            notices.append((await http.get(servers_page.SERVERS_PATH)).text)
        for word, message in (("all", "Four."), ("red", "Five."), ("blue", "Six.")):
            reply = await send(http, SERVER_CHAT, token, message, coalition=word)
            assert reply.status_code == 303, (word, reply.text)
            notices.append((await http.get(servers_page.SERVERS_PATH)).text)

    assert a.calls == [("popup", Coalition.ALL, "One.", -1, SENDER),
                       ("popup", Coalition.RED, "Two.", -1, SENDER),
                       ("popup", Coalition.BLUE, "Three.", -1, SENDER),
                       ("chat", Coalition.ALL, "Four.", SENDER),
                       ("chat", Coalition.RED, "Five.", SENDER),
                       ("chat", Coalition.BLUE, "Six.", SENDER)]
    # each notice names the audience the message ACTUALLY reached
    assert notified(notices[0], "Popup sent to everyone on server 'SRS-1'."), notices[0]
    assert notified(notices[1], "Popup sent to the Red coalition on server 'SRS-1'."), notices[1]
    assert notified(notices[2], "Popup sent to the Blue coalition on server 'SRS-1'."), notices[2]
    assert notified(notices[3], "Chat message sent to everyone on server 'SRS-1'."), notices[3]
    assert notified(notices[4], "Chat message sent to the Red coalition on server 'SRS-1'."), \
        notices[4]
    assert notified(notices[5], "Chat message sent to the Blue coalition on server 'SRS-1'."), \
        notices[5]
    assert [call[0] for call in recording_bot.calls] == [
        "Popup sent to everyone on server 'SRS-1'.",
        "Popup sent to the Red coalition on server 'SRS-1'.",
        "Popup sent to the Blue coalition on server 'SRS-1'.",
        "Chat message sent to everyone on server 'SRS-1'.",
        "Chat message sent to the Red coalition on server 'SRS-1'.",
        "Chat message sent to the Blue coalition on server 'SRS-1'.",
    ]


# ------------------------------------------------------------------- 5. the crafted POST + the audit

@pytest.mark.asyncio
async def test_a_crafted_coalition_in_the_body_is_refused_and_audited(config_dir, clean_registry,
                                                                     recording_bot):
    """ACCEPTANCE — the recipient is the ACTION's allow-list, not a form field: a body that states
    something else is refused, nothing reaches the domain, and the attempt leaves ONE entry."""
    cluster = message_fleet(coalitions=True)
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        popup = await send(http, SERVER_POPUP, token, "Hi.", coalition="neutral")
        assert popup.status_code == 303
        refused_popup = (await http.get(servers_page.SERVERS_PATH)).text
        chat = await send(http, SERVER_CHAT, token, "Hi.", coalition="green")
        assert chat.status_code == 303
        refused_chat = (await http.get(servers_page.SERVERS_PATH)).text

    assert a.calls == [], "a crafted recipient reached the domain"
    assert len(recording_bot.calls) == 2, recording_bot.calls
    assert "Unknown recipient 'neutral'" in recording_bot.calls[0][0]
    assert "Unknown recipient 'green'" in recording_bot.calls[1][0]
    assert notified(refused_popup, "Unknown recipient 'neutral'"), refused_popup
    assert notified(refused_chat, "Unknown recipient 'green'"), refused_chat


@pytest.mark.asyncio
async def test_a_coalition_post_is_delivered_on_a_server_that_declares_no_coalitions(
        config_dir, clean_registry, recording_bot):
    """The route hands the received coalition to the action and it IS delivered — the config block is
    no gate, so a server whose ``servers.yaml`` entry carries none still gets the message, and the
    write leaves its ONE audit entry."""
    cluster = message_fleet(coalitions=False)
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        reply = await send(http, SERVER_POPUP, token, "Hi.", coalition="red")
        assert reply.status_code == 303
        after = (await http.get(servers_page.SERVERS_PATH)).text

    assert a.calls == [("popup", Coalition.RED, "Hi.", -1, SENDER)], a.calls
    assert len(recording_bot.calls) == 1, recording_bot.calls
    assert "Popup sent to the Red coalition on server 'SRS-1'." in recording_bot.calls[0][0]
    assert notified(after, "Popup sent to the Red coalition on server 'SRS-1'."), after


@pytest.mark.asyncio
async def test_a_crafted_mode_in_the_body_cannot_move_the_write(config_dir, clean_registry,
                                                               recording_bot):
    """The MODE is the control's own fact (``WriteAction.constants``), not a form field.

    The chat control's dialog is posted with ``mode=popup`` in its body; because the console passes
    the DECLARATION's value and never the posted one, the domain still receives a chat line.
    """
    cluster = message_fleet()
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        dialog = await open_dialog(http, SERVER_CHAT, token)
        reply = await http.post(SERVER_CHAT, data={"server": SERVER, "message": "Look alive.",
                                                   "coalition": "all", "mode": "popup",
                                                   "_csrf_token": token,
                                                   "_confirm_token": confirm_of(dialog)})
        assert reply.status_code == 303, reply.text

    assert a.calls == [("chat", Coalition.ALL, "Look alive.", SENDER)]


@pytest.mark.asyncio
async def test_a_post_that_skips_the_dialog_is_refused(config_dir, clean_registry, recording_bot):
    """``POST`` is not ``write``: the row posts to the dialog, the dialog's form carries the one-shot
    token, and a caller who skips it is refused before the action runs."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    a = cluster.servers[0]
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        skipped = await http.post(SERVER_CHAT, data={"server": SERVER, "message": "hi",
                                                     "coalition": "all", "_csrf_token": token},
                                  headers={"Accept": "text/html"})
    assert skipped.status_code == 403, skipped.text
    assert a.calls == [], "a POST that skipped the dialog reached the write"
    assert recording_bot.calls == []


# ------------------------------------------------------------------------------- 5b. the copy

#: the untrue claim the dialogs and the catalogs carried, in every spelling they had
FALSE_CLAIMS = (
    "DCS has no coalition chat",
    "a chat line always reaches the whole server",
    "a chat line cannot be addressed to one side",
    "the whole server reads",
    "a chat line the whole server reads",
    "Send a chat line to everyone on",
)


def dialog_copy(html: str) -> str:
    """The dialog's own prose — its warning block, its detail and the helps, as rendered."""
    parts = re.findall(r'(?:<div class="dlg-warn">.*?</div>|<p class="dlg-what">.*?</p>'
                       r'|<span class="hint"[^>]*>.*?</span>)', html, re.S)
    assert parts, f"the dialog rendered no copy at all:\n{html}"
    return " ".join(parts)


@pytest.mark.asyncio
async def test_the_chat_dialog_states_what_the_chosen_recipient_does(config_dir, clean_registry):
    """ACCEPTANCE — no sentence claims a server-wide audience or that DCS cannot address a side.

    The audience is a choice on the dialog, so its copy names the CHOICE ("the recipients you choose
    below") instead of one of the two outcomes.
    """
    app = build(config_dir, message_fleet(coalitions=True))
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, servers_page.SERVERS_PATH)
        token = token_of(page)
        text = dialog_copy(await open_dialog(http, SERVER_CHAT, token))

    for claim in FALSE_CLAIMS:
        assert claim not in text, f"the chat dialog still claims '{claim}':\n{text}"
    assert "The recipients you choose below read this line." in text, text
    assert "or of the one side you choose below" in text, text
    for claim in FALSE_CLAIMS:
        assert claim not in page, f"the strip still claims '{claim}':\n{page}"


def test_no_catalog_carries_the_claim_that_dcs_has_no_coalition_chat():
    """ACCEPTANCE — the untrue sentence is gone from every catalog, obsolete entries included.

    ``pybabel update`` keeps a replaced msgid as a ``#~`` obsolete entry; gettext ignores those, so a
    catalog can look clean while the sentence is still in the file for the next extraction to revive.
    The scan covers the whole file, not only the live entries.
    """
    import glob

    catalogs = sorted(glob.glob("locale/*/LC_MESSAGES/webui.po"))
    assert catalogs, "no catalogs found - the scan would pass for the wrong reason"
    for path in catalogs:
        body = open(path, encoding="utf-8").read()
        for claim in FALSE_CLAIMS:
            assert claim not in body, f"{path} still carries '{claim}'"


# ------------------------------------------------------------------------- 6. the omitted controls

@pytest.mark.asyncio
async def test_an_identity_without_the_capability_is_offered_neither_glyph_and_refused(
        config_dir, clean_registry, recording_bot):
    """No capability, no glyph — and the gate refuses a hand-crafted POST before the handler."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "plain")                 # ``DCS`` only: not a console reader
        page = await http.get(servers_page.SERVERS_PATH)
        assert page.status_code == 403, page.text
        assert "act-strip" not in page.text and "act-btn" not in page.text
        assert SERVER_CHAT not in page.text and SERVER_POPUP not in page.text
        reply = await http.post(SERVER_CHAT, data={"server": SERVER, "message": "hi",
                                                   "coalition": "all", "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"
    assert recording_bot.calls == []


@pytest.mark.asyncio
async def test_a_plugin_the_install_does_not_have_offers_neither_glyph(config_dir, clean_registry):
    """The action layer absent (no ``mission`` plugin): both controls are omitted, not rendered dead."""
    app = build(config_dir, message_fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, servers_page.SERVERS_PATH)
    assert not forms_for(html, SERVER_CHAT + CONFIRM)
    assert not forms_for(html, SERVER_POPUP + CONFIRM)


@pytest.mark.asyncio
async def test_a_manager_messages_only_their_own_server(config_dir, clean_registry, recording_bot):
    """The scope, on the strip: no control for somebody else's server."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    own, foreign = cluster.servers[0], cluster.servers[1]

    async with client(app) as http:
        await sign_in(http, "hoster")                # declares scope ["Host A"]
        html = await open_page(http, servers_page.SERVERS_PATH)
        assert forms_for(html, SERVER_POPUP + CONFIRM), "their own row offers no control"
        reply = await send(http, SERVER_POPUP, token_of(html), "Hi.")
        assert reply.status_code == 303

    assert own.calls == [("popup", Coalition.ALL, "Hi.", -1, "hoster")]
    assert foreign.calls == []


# ----------------------------------------------------------------------------- 7. the deploy pins

def test_the_console_and_the_action_agree_on_the_field_length():
    """The console's control cap and the ACTION's own limit are ONE number, pinned to each other.

    ``pages/actions.py`` may not import the plugin (a console whose plugin is absent still renders
    its pages), so the two constants are declared separately and the drift would be silent in both
    directions — a form the action refuses, or a limit the form never enforces.
    """
    from plugins.mission.actions import MAX_MESSAGE_LENGTH

    assert actions_page.MESSAGE_FIELD_MAX == MAX_MESSAGE_LENGTH


def test_the_console_offers_exactly_the_recipients_the_action_can_deliver():
    """DRIFT PIN — each control's SELECT and the ACTION's allow-list are ONE vocabulary.

    The console owns the labels and the action owns the values, so the two could drift: a control
    offering an option the action refuses is a dead control, and an option the action accepts but the
    console never offers is unreachable. The pin is exact, in both directions, per mode.
    """
    from plugins.mission.actions import (SERVER_MESSAGE_COALITIONS, SERVER_MESSAGE_LABELS,
                                         SERVER_MESSAGE_RECIPIENTS)

    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    for key, mode in (("chat", "chat"), ("popup", "popup")):
        declared = tuple(option.value for option in by_key[key].select.options)
        assert declared == SERVER_MESSAGE_RECIPIENTS[mode], key
        for word in declared:
            assert word in SERVER_MESSAGE_COALITIONS, word
            assert word in SERVER_MESSAGE_LABELS, word
    # the three labels the card names, verbatim, and their values
    labels = {option.value: option.label for option in by_key["popup"].select.options}
    assert labels == {"all": "Everyone", "red": "Red coalition", "blue": "Blue coalition"}


def test_the_domain_fans_a_coalition_chat_out_to_that_sides_active_players():
    """THE EVIDENCE behind chat's three recipients: the domain delivers ``Coalition.ALL`` as ONE
    server-wide line and every other coalition as a FAN-OUT — one targeted line per active player of
    that side.

    ``Server.sendChatMessage`` is the method the action calls, and the enumeration (the real
    ``Server.get_active_players``) and the delivery (the real ``Player.sendChatMessage``) are its own.
    The full matrix — the other side left out, an inactive player of this side left out, the empty
    server, the multiline split — is in ``tests/test_mission_server_chat_fanout.py``.
    """
    import asyncio

    from core.data.const import Side
    from core.data.player import Player
    from core.data.server import Server

    class _Wire:
        """The server double: the REAL enumeration, plus the DCS wire recorded."""

        get_active_players = Server.get_active_players

        def __init__(self, players):
            self.players = players
            self.sent: list[dict] = []

        async def send_to_dcs(self, payload):
            self.sent.append(payload)

    class _Player:
        """A player carrying the REAL delivery method, so the payloads are the domain's own."""

        sendChatMessage = Player.sendChatMessage

        def __init__(self, ucid, player_id, side, *, active=True):
            self.ucid = ucid
            self.id = player_id
            self.side = side
            self.active = active
            self.server: "_Wire | None" = None

    wire = _Wire({})
    red = _Player("u-red", 101, Side.RED)
    blue = _Player("u-blue", 102, Side.BLUE)
    wire.players = {"u-red": red, "u-blue": blue}
    for player in (red, blue):
        player.server = wire

    async def drive():
        await Server.sendChatMessage(wire, Coalition.ALL, "hi")
        await Server.sendChatMessage(wire, Coalition.RED, "hi", SENDER)

    asyncio.run(drive())
    assert wire.sent == [{"command": "sendChatMessage", "from": None, "message": "hi"},
                         {"command": "sendChatMessage", "to": 101, "from": SENDER, "message": "hi"}]


def test_the_action_module_is_importable_without_a_running_bot():
    """The action is a plain module-level function: no bot, no service, no web import needed.

    It matters to the DEPLOY list: an existing installation must receive
    ``plugins/mission/actions.py`` for the console's message controls to exist at all, and this test
    is what says that file carries no runtime dependency the install does not already have.
    """
    from plugins.mission.actions import message_server

    assert message_server.__qualname__ == "message_server"
    source = inspect.getsource(message_server)
    assert "services.webservice" not in source, "the action reaches into the web layer"


# ------------------------------------------------- 7. the field, and the pulse it does NOT start

@pytest.mark.asyncio
async def test_the_server_message_field_is_a_multi_line_textarea(config_dir, clean_registry):
    """The message is prose: both server dialogs collect it in a TEXTAREA, at the declared cap."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        for path in (SERVER_CHAT, SERVER_POPUP):
            dialog = await open_dialog(http, path, token)
            match = re.search(r"<textarea[^>]*>", dialog)
            assert match, f"{path} renders no textarea:\n{dialog}"
            tag = match.group(0)
            assert 'name="message"' in tag, tag
            assert 'rows="5"' in tag, tag
            assert f'maxlength="{actions_page.MESSAGE_FIELD_MAX}"' in tag, tag
            assert "required" in tag, tag


@pytest.mark.asyncio
async def test_a_message_write_starts_no_pulse(config_dir, clean_registry):
    """ACCEPTANCE — the glyph must NOT blink after a message.

    The pulse is the expectation store (``pages.actions.awaiting_change``), and an expectation for a
    message could only ever end on the 120-second ceiling, because a message moves no status, flag or
    revision: the control therefore declared ``AWAIT_OBSERVABLE_NONE`` and the route records nothing,
    so the page the write lands on renders no busy control. A page can only be seen to be un-busy by
    rendering it, which is what this asserts — over the store AND the markup.
    """
    for action in actions_page.SERVER_ACTIONS:
        if action.key in ("chat", "popup"):
            assert action.observable == actions_page.AWAIT_OBSERVABLE_NONE, action.key

    cluster = message_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        reply = await send(http, SERVER_POPUP, token, "Hi.")
        assert reply.status_code == 303
        after = (await http.get(servers_page.SERVERS_PATH)).text

    assert actions_page.awaiting_store() == {}, "a message write left an expectation behind"
    assert "act-btn busy" not in after, "the pressed glyph is still blinking"


# ------------------------------------------------- 8. the two ways out share the bottom line

@pytest.mark.asyncio
async def test_the_two_ways_out_share_the_bottom_line_of_a_dialog_with_fields(config_dir,
                                                                             clean_registry):
    """ACCEPTANCE — a dialog that asks for several lines (a message) keeps Cancel and Send on the SAME
    last line of the dialog, BELOW its fields.

    The footer used to be one flex row — [Cancel] [the form: the fields, then the button] — so a
    textarea pushed the primary button down the row while the cancel link stayed centred beside the
    textarea. The fields now come first and the two controls share the last line.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http, servers_page.SERVERS_PATH))
        dialog = await open_dialog(http, SERVER_CHAT, token)

    footer = dialog[dialog.index('class="ft"'):]
    form_at = footer.index('<form id="dlg-form"')
    field_at = footer.index("<textarea")
    buttons_at = footer.index('class="dlg-buttons"')
    cancel_at = footer.index(">Cancel</a>", buttons_at)
    send_at = footer.index('type="submit"', buttons_at)
    assert form_at < field_at < buttons_at < cancel_at < send_at, \
        "Cancel and the primary are not the last line of the dialog, below its fields"
    row = footer[buttons_at:footer.index("</div>", buttons_at)]
    assert 'class="btn" href' in row, row
    assert 'type="submit"' in row, row


def test_the_button_row_is_a_shell_rule_and_the_keyboard_starts_on_cancel():
    """The shape is the template's; the ROW and the keyboard rule belong to the shell and the page.

    The trap must start on CANCEL: with the fields above the row, the first focusable can be a text
    field, and Enter in one of those is an implicit SUBMIT of the dialog's action.
    """
    sheet = (REPO / "services" / "webservice" / "static" / "shell.css").read_text(encoding="utf-8")
    rule = re.search(r"\.dlg-buttons \{([^}]*)\}", sheet)
    assert rule, "the button row has no rule in the stylesheet"
    assert "display: flex" in rule.group(1) and "stretch" in rule.group(1), rule.group(1)
    template = (REPO / "services" / "webservice" / "templates" / "confirm.html").read_text(
        encoding="utf-8")
    assert 'var cancel = dialog.querySelector("a.btn[href]");' in template, \
        "the keyboard trap must start on Cancel, never on a text field"
