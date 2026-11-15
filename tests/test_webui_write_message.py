"""MESSAGE a player (``players.message``) through the console's two message controls.

One section per acceptance criterion: the ACTION (empty/whitespace is refused WITHOUT touching the
domain method -- ``send_to_dcs`` is never reached; both modes reach the right method with the right
arguments; "no such player" is a typed refusal); the ROUTE + CONTROL (a DCS Admin messages a player
through the glyph the page rendered and the dialog it opens, the notice says who it reached, the audit
names the WEB actor; the mode is the control's own, never a form field); the SCOPE
(a manager acts on their OWN server's players only, and a crafted POST naming another server's player is
refused INDISTINGUISHABLY from a name that does not exist); the OMITTED control; CSRF; and the AUDIT (all
four shapes leave ONE entry each, each naming the actor and the attempt). Driven through the real app
with the real local login backend and the stub cluster; no Discord, no database, no network."""
from __future__ import annotations

import re
import types

import pytest

from core import Status
import core.actions as actions_module
from core.action_results import PlayerActionResult
from core.actions import ActionContext, action_available, action_registry
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"

# ONE harness for the console's write actions: the pause pilot's doubles are reused, not copied.
# ``config_dir``, ``clean_registry`` and ``recording_bot`` are FIXTURES and are imported as such —
# a pytest fixture found in the module's namespace is a fixture of this module.
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    Cluster, build, clean_registry, client, config_dir, instance, recording_bot,
    server as server_double, sign_in, token_of,
)

#: two ACTIVE players, one per side of the scope: ``Host A`` runs SRS-1, ``Host B`` runs SRS-B
UCID_A = "4b1f9c22-0000-4000-8000-000000000001"
UCID_B = "9e77c011-0000-4000-8000-000000000002"

#: the sender label the route passes: the identity's own shown name (``display_name`` for the local
#: backend), mirroring the Discord commands' ``interaction.user.display_name``
SENDER = "mod1"

#: the two message controls — one per mode — and the dialog suffix their glyphs post to
CHAT = actions_page.CHAT_PATH
POPUP = actions_page.POPUP_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX


class FakePlayer:
    """The ``Player`` double: both message methods are awaitable, RECORD the arguments they were
    given, and forward the DCS payload the real methods build to the server's ``send_to_dcs``.

    Both halves matter. The recorded tuple is what a test asserts the ACTION passed —
    ``sendPopupMessage`` mirrors the real signature (``message, timeout=-1, sender=None``), so the
    timeout the action left to the server's own default is visible as ``-1``. The forwarded payload
    is what makes "the refusal did not touch DCS" literal: the action can only reach
    ``server.send_to_dcs`` through these methods (``core/data/player.py:244-271``), so a refusal
    leaves ``server.dcs_calls`` empty.
    """

    def __init__(self, name, ucid, *, active=True):
        self.name = name
        self.display_name = name
        self.ucid = ucid
        self.active = active
        self.unit_display_name = "A-10C II"
        self.group_name = "Hog 1"
        self.id = 101
        self.unit_name = "unit-101"
        self.server = None  # bound by server_with_players
        self.calls: list[tuple] = []

    async def sendPopupMessage(self, message, timeout=-1, sender=None):
        self.calls.append(("popup", message, timeout, sender))
        await self.server.send_to_dcs({"command": "sendPopupMessage", "from": sender, "to": "unit",
                                       "id": self.unit_name, "message": message, "time": timeout})

    async def sendChatMessage(self, message, sender=None):
        self.calls.append(("chat", message, sender))
        for line in message.splitlines():
            await self.server.send_to_dcs({"command": "sendChatMessage", "to": self.id,
                                           "from": sender, "message": line})


def _lookup(server, ucid, active):
    """``Server.get_player``'s UCID branch, which is the only one the action uses."""
    player = server.players.get(ucid)
    if player is None or (active is not None and player.active != active):
        return None
    return player


def server_with_players(name, managed_by=None, *, players=(), status=Status.RUNNING,
                        node_name="dcs-a-01", instance_name="Host-A-Instance"):
    """The pause pilot's server double, plus the three things a player write needs: ``players``,
    ``get_player`` (the same ``Server`` API the Discord picker uses) and a recording ``send_to_dcs``.
    """
    srv = server_double(name, managed_by, status=status, node_name=node_name,
                        instance=instance_name)
    srv.players = {player.ucid: player for player in players}
    for player in players:
        player.server = srv
 # the domain call a message ultimately makes: recorded per server, and never reaching a DCS
 # installation (so "the refusal sent nothing" is asserted on the boundary the action really uses)
    dcs_calls: list[dict] = []

    async def record_dcs(payload):
        dcs_calls.append(payload)

    srv.dcs_calls = dcs_calls
    srv.send_to_dcs = record_dcs
    srv.get_player = lambda ucid=None, active=None: _lookup(srv, ucid, active)
    return srv


def message_fleet():
    """A (Host A) and B (Host B), each with one ACTIVE player — the scope's two sides."""
    a = server_with_players("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    b = server_with_players("SRS-B", ["Host B"], players=[FakePlayer("Viper", UCID_B)],
                            node_name="dcs-b-01", instance_name="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


def direct_ctx(servers):
    """An ``ActionContext`` with no scope stated: the ACTION is driven directly (as MCP would)."""
    return ActionContext(node=types.SimpleNamespace(name="NODE"),
                         bus=types.SimpleNamespace(servers=dict(servers)))


def forms_for(html: str, path: str) -> list[str]:
    """Every rendered form whose ``action`` is *path* — the markup the control actually is."""
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


def notified(html: str, sentence: str) -> bool:
    """Whether the page's notice carries *sentence* — Jinja escapes it, so both spellings count.

    The notice is rendered by ``_notice.html`` from the action's own message, and a message naming a
    server carries an apostrophe: asserting only the raw form would fail on a page that is showing
    exactly the right sentence.
    """
    return sentence in html or sentence.replace("'", "&#39;") in html


def confirm_of(dialog: str) -> str:
    """The one-shot token the rendered dialog's form carries."""
    match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog)
    assert match, "the dialog rendered no confirm token"
    return match.group(1)


async def open_dialog(http, path: str, token: str, server: str = "SRS-1",
                      ucid: str = UCID_A) -> str:
    """Press one message glyph: the row posts to its DIALOG, which asks for the message."""
    reply = await http.post(path + CONFIRM,
                            data={"server": server, "ucid": ucid, "_csrf_token": token})
    assert reply.status_code == 200, reply.text
    return reply.text


async def send(http, path: str, token: str, message: str, server: str = "SRS-1",
               ucid: str = UCID_A):
    """The WHOLE round trip a glyph press is: open the dialog, then post its form."""
    dialog = await open_dialog(http, path, token, server, ucid)
    return await http.post(path, data={"server": server, "ucid": ucid, "message": message,
                                       "_csrf_token": token,
                                       "_confirm_token": confirm_of(dialog)})


# ---------------------------------------------------------------------- 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_message_action_in_its_own_process(config_dir,
                                                                             clean_registry):
    """``action_available("message_player")`` through the shell's OWN install.

    The console renders a control from ``action_available``; without the shell's discovery wiring the
    registry stays empty and every control is silently omitted while the suite stays green. Asserted
    through the shell, never through a helper that pre-registers.
    """
    assert action_available("message_player") is False, "the registry must start EMPTY here"
    build(config_dir, message_fleet())
    assert action_available("message_player") is True
    assert "message_player" in action_registry()


@pytest.mark.asyncio
async def test_the_control_is_omitted_when_the_mission_plugin_is_not_loaded(config_dir,
                                                                          clean_registry):
    """The control for the discovery test above: no plugin, no action, no control."""
    build(config_dir, message_fleet(), plugins=())
    assert action_available("message_player") is False


# ------------------------------------------------------------------------ 2. the action itself

@pytest.mark.asyncio
async def test_the_action_refuses_an_empty_or_whitespace_message_without_touching_dcs():
    """ACCEPTANCE — a blank message is a TYPED refusal and the domain call does NOT happen."""
    from plugins.mission.actions import message_player

    player = FakePlayer("Fragmagnet", UCID_A)
    srv = server_with_players("SRS-1", players=[player])
    ctx = direct_ctx({"SRS-1": srv})

    for blank in ("", "   ", "\t", "\n  \t\n"):
        result = await message_player(ctx, "SRS-1", player.ucid, blank)
        assert isinstance(result, PlayerActionResult)
        assert result.success is False, f"{blank!r} was accepted"
        assert result.message == "Message must not be empty."
        assert player.calls == [], f"{blank!r} reached the domain method"
        assert srv.dcs_calls == [], f"{blank!r} reached the server's send_to_dcs"

 # the positive control: a message with content DOES reach it, so the assertion above measures
 # the validation rather than a double that never records anything
    assert (await message_player(ctx, "SRS-1", player.ucid, "hello")).success is True
    assert player.calls == [("popup", "hello", -1, None)]
    assert srv.dcs_calls == [{"command": "sendPopupMessage", "from": None, "to": "unit",
                              "id": player.unit_name, "message": "hello", "time": -1}]


@pytest.mark.asyncio
async def test_the_action_refuses_an_over_long_message_and_an_unknown_mode():
    """The same validation shape for the other two refusals, neither of which sends anything."""
    from plugins.mission.actions import MAX_MESSAGE_LENGTH, message_player

    player = FakePlayer("Fragmagnet", UCID_A)
    srv = server_with_players("SRS-1", players=[player])
    ctx = direct_ctx({"SRS-1": srv})

    too_long = await message_player(ctx, "SRS-1", player.ucid, "x" * (MAX_MESSAGE_LENGTH + 1))
    assert too_long.success is False
    assert str(MAX_MESSAGE_LENGTH) in too_long.message
    assert player.calls == [] and srv.dcs_calls == []

    unknown = await message_player(ctx, "SRS-1", player.ucid, "hello", "shout")
    assert unknown.success is False
    assert "shout" in unknown.message and "popup" in unknown.message
    assert player.calls == [] and srv.dcs_calls == []

 # the boundary itself is ACCEPTED — the limit is a maximum, not an exclusive bound
    at_limit = await message_player(ctx, "SRS-1", player.ucid, "x" * MAX_MESSAGE_LENGTH)
    assert at_limit.success is True
    assert player.calls == [("popup", "x" * MAX_MESSAGE_LENGTH, -1, None)]


@pytest.mark.asyncio
async def test_both_modes_reach_the_right_domain_method_with_the_right_arguments():
    """ACCEPTANCE — ``popup`` calls ``sendPopupMessage``, ``chat`` calls ``sendChatMessage``."""
    from plugins.mission.actions import message_player

    player = FakePlayer("Fragmagnet", UCID_A)
    ctx = direct_ctx({"SRS-1": server_with_players("SRS-1", players=[player])})

    popup = await message_player(ctx, "SRS-1", player.ucid, "Check your six.", "popup", SENDER)
    assert popup.success is True
    assert isinstance(popup, PlayerActionResult)
    assert (popup.player_name, popup.ucid) == ("Fragmagnet", UCID_A)
    assert player.calls == [("popup", "Check your six.", -1, SENDER)]

    chat = await message_player(ctx, "SRS-1", player.ucid, "Look alive.", "chat", SENDER)
    assert chat.success is True
    assert player.calls == [("popup", "Check your six.", -1, SENDER),
                            ("chat", "Look alive.", SENDER)]


@pytest.mark.asyncio
async def test_a_player_who_is_not_on_the_server_is_a_typed_refusal():
    """``Server.get_player(active=True)`` is the guard, exactly as the Discord picker is."""
    from plugins.mission.actions import message_player

    player = FakePlayer("Fragmagnet", UCID_A)
    ctx = direct_ctx({"SRS-1": server_with_players("SRS-1", players=[player])})

    result = await message_player(ctx, "SRS-1", UCID_B, "hello")
    assert result.success is False
    assert result.message == "Player not found on server 'SRS-1'."
    assert player.calls == []

 # a player who is CONNECTED but not in a slot is not reachable either
    player.active = False
    inactive = await message_player(ctx, "SRS-1", UCID_A, "hello")
    assert inactive.success is False
    assert player.calls == []


# --------------------------------------------------------------- 3. the control and the round trip

@pytest.mark.asyncio
async def test_a_dcs_admin_messages_a_player_through_the_control_the_page_rendered(
        config_dir, clean_registry, recording_bot):
    """ACCEPTANCE — the round trip: glyph, dialog, POST, domain call, notice, audit."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await http.get(ROSTER)
        assert page.status_code == 200, page.text
        html = page.text

        forms = forms_for(html, POPUP + CONFIRM)
        assert forms, "the Players page offered no popup control"
        form = forms[0]
 # the row carries the glyph ONLY: no field, no mode button — the dialog asks for the message
        assert 'name="message"' not in form and 'name="mode"' not in form
        assert 'name="server"' in form and 'name="ucid"' in form
        assert f'value="{UCID_A}"' in form
 # the target never travels in a URL
        assert "?server=" not in html and "?ucid=" not in html

        dialog = await open_dialog(http, POPUP, token_of(html))
        assert 'name="message"' in dialog, "the dialog asks for the message"
        reply = await http.post(POPUP, data={"server": "SRS-1", "ucid": UCID_A,
                                             "message": "Welcome back, check your six.",
                                             "_csrf_token": token_of(html),
                                             "_confirm_token": confirm_of(dialog)})
        assert reply.status_code == 303, reply.text
        # the POST carries no origin, so the answer is the SECTION default: a player write returns to
        # the Players page, whether or not the control it came from was rendered there.
        assert reply.headers["location"] == players_page.PLAYERS_PATH
        assert player.calls == [("popup", "Welcome back, check your six.", -1, SENDER)]
 # …and the whole way to the boundary: one DCS command, addressed to the player's UNIT
        assert cluster.servers[0].dcs_calls == [
            {"command": "sendPopupMessage", "from": SENDER, "to": "unit", "id": player.unit_name,
             "message": "Welcome back, check your six.", "time": -1}]

        after = await http.get(ROSTER)
        assert notified(after.text, "Popup sent to Fragmagnet on server 'SRS-1'."), after.text

    assert recording_bot.calls[0][0] == \
        "Popup sent to Fragmagnet on server 'SRS-1'."
    assert recording_bot.calls[0][1]["server"] is cluster.servers[0]


@pytest.mark.asyncio
async def test_the_chat_control_is_its_own_control_and_reaches_the_chat_method(config_dir,
                                                                              clean_registry,
                                                                              recording_bot):
    """Two controls, one per mode: ``chat`` is a glyph of its own, and its own dialog."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]

    async with client(app) as http:
        await sign_in(http, "mod1")
        html = (await http.get(ROSTER)).text
        assert forms_for(html, CHAT + CONFIRM), "the chat control posts to its dialog"
        reply = await send(http, CHAT, token_of(html), "Look alive.")
        assert reply.status_code == 303
        after = (await http.get(ROSTER)).text

    assert player.calls == [("chat", "Look alive.", SENDER)]
    assert notified(after, "Chat message sent to Fragmagnet on server 'SRS-1'."), after


# ------------------------------------------------------------------------------------ 4. the scope

@pytest.mark.asyncio
async def test_a_manager_messages_only_the_players_on_their_own_servers(config_dir, clean_registry,
                                                                       recording_bot):
    """A manager sees and can use the control only for THEIR players.

    ``hoster`` holds NO console role (``DCS`` only) and reaches the write through the scope alone
    (``scope_grants``).
    """
    cluster = message_fleet()
    app = build(config_dir, cluster)
    own = cluster.servers[0].players[UCID_A]
    foreign = cluster.servers[1].players[UCID_B]

    async with client(app) as http:
        await sign_in(http, "hoster")               # declares scope ["Host A"]
        page = await http.get(ROSTER)
        assert page.status_code == 200, page.text
        html = page.text
        assert forms_for(html, POPUP + CONFIRM), "their own row offers no control"
        assert f'value="{UCID_B}"' not in html, "a control was built for somebody else's player"

        reply = await send(http, POPUP, token_of(html), "Hi.")
        assert reply.status_code == 303
        assert own.calls == [("popup", "Hi.", -1, "hoster")]

    assert foreign.calls == []
    assert recording_bot.calls[0][0] == \
        "Popup sent to Fragmagnet on server 'SRS-1'."


@pytest.mark.asyncio
async def test_a_crafted_post_at_another_servers_player_is_refused_indistinguishably(
        config_dir, clean_registry, recording_bot):
    """ACCEPTANCE — the non-disclosure property, on the player write.

    A name outside the caller's scope and a name that does not exist answer IDENTICALLY (403, one
    body): otherwise a hoster could enumerate the fleet — and its players — by probing names.
    """
    cluster = message_fleet()
    app = build(config_dir, cluster)
    a, b = cluster.servers[0], cluster.servers[1]

    async with client(app) as http:
        await sign_in(http, "hoster-b")             # manages Host B
        html = (await http.get(ROSTER)).text
        token = token_of(html)

        foreign = await http.post(POPUP + CONFIRM,
                                  data={"server": "SRS-1", "ucid": UCID_A, "_csrf_token": token})
        missing = await http.post(POPUP + CONFIRM,
                                  data={"server": "NOPE-9", "ucid": UCID_A, "_csrf_token": token})

        assert foreign.status_code == 403
        assert missing.status_code == 403
        assert foreign.text == missing.text, "the two refusals disclose a difference"
        assert "SRS-1" not in foreign.text and UCID_A not in foreign.text

 # a player who exists NOWHERE, named on the caller's OWN server: the dialog loader refuses it
 # with the answer a player who is not on that server gets, and says nothing about the other one
        on_own = await http.post(POPUP + CONFIRM,
                                 data={"server": "SRS-B", "ucid": UCID_A, "_csrf_token": token})
        assert on_own.status_code == 404
        assert "SRS-1" not in on_own.text

    assert a.players[UCID_A].calls == [] and b.players[UCID_B].calls == []


# ------------------------------------------------------------------------- 5. the omitted control

@pytest.mark.asyncio
async def test_an_identity_without_the_capability_is_offered_no_control_and_refused(
        config_dir, clean_registry, recording_bot):
    """ACCEPTANCE — no control in the markup, and a hand-crafted POST is the gate's refusal."""
    cluster = message_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "plain")                # ``DCS`` only: not a console reader
        page = await http.get(ROSTER)
        assert page.status_code == 403
        assert "act-btn" not in page.text and "act-strip" not in page.text
        assert actions_page.CHAT_PATH not in page.text
        assert actions_page.POPUP_PATH not in page.text

        reply = await http.post(POPUP, data={"server": "SRS-1", "ucid": UCID_A, "message": "Hi.",
                                            "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"
        assert "Internal Server Error" not in reply.text

    assert cluster.servers[0].players[UCID_A].calls == []
    assert recording_bot.calls == []


@pytest.mark.asyncio
async def test_an_unavailable_action_omits_the_control_and_answers_a_typed_refusal(
        config_dir, clean_registry, recording_bot):
    """The action layer absent (the plugin is not installed): omitted control, typed refusal."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    actions_module._ACTIONS.clear()       # the ``mission`` plugin is not on this install

    async with client(app) as http:
        await sign_in(http, "mod1")
        html = (await http.get(ROSTER)).text
        assert not forms_for(html, POPUP + CONFIRM)

        reply = await send(http, POPUP, token_of(html), "Hi.")
        assert reply.status_code == 303, "a typed refusal is a rendered banner, not an error"
        after = (await http.get(ROSTER)).text
        assert "not available" in after

    assert cluster.servers[0].players[UCID_A].calls == []


# ---------------------------------------------------------------------------------- 6. the CSRF

@pytest.mark.asyncio
async def test_a_post_without_a_valid_csrf_token_is_refused_and_nothing_runs(config_dir,
                                                                           clean_registry,
                                                                           recording_bot):
    """A write carries the SESSION's token; absent, or another session's, is a refusal."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    browser = {"Accept": "text/html"}
    body = {"server": "SRS-1", "ucid": UCID_A, "message": "Hi."}

    async with client(app) as http:
        await sign_in(http, "mod1")
        await http.get(ROSTER)

        no_token = await http.post(POPUP, data=body, headers=browser)
        assert no_token.status_code == 403
        assert "nothing was saved" in no_token.text
        assert UCID_A not in no_token.text

        foreign = await http.post(POPUP, data={**body, "_csrf_token": "not-this-session"},
                                  headers=browser)
        assert foreign.status_code == 403
        assert "nothing was saved" in foreign.text

        api = await http.post(POPUP, data={**body, "_csrf_token": "not-this-session"})
        assert api.status_code == 403
        assert api.json()["detail"] == "CSRF token missing or invalid."

    assert cluster.servers[0].players[UCID_A].calls == []
    assert recording_bot.calls == []


# --------------------------------------------------------------------------------- 7. the audit

@pytest.mark.asyncio
async def test_the_audit_records_the_attempt_in_all_reachable_shapes(config_dir, clean_registry,
                                                                    recording_bot):
    """ONE entry per attempt, whatever the outcome.

    Three shapes, each reached through the route: a popup success, a chat success (each through its
    own dialog), and the action's own typed refusal (an empty message). A fourth — the route's
    "server not found" — is no longer reachable from a rendered page: the dialog loader refuses a
    name that does not exist with its own 404 before any token is minted, exactly like kick and ban.
    """
    cluster = message_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]

    async with client(app) as http:
        await sign_in(http, "specialk")             # Admin: unscoped by rule
        html = (await http.get(ROSTER)).text
        token = token_of(html)

        popup = await send(http, POPUP, token, "One.")
        assert popup.status_code == 303
        chat = await send(http, CHAT, token, "Two.")
        assert chat.status_code == 303
        refused = await send(http, POPUP, token, "   ")
        assert refused.status_code == 303

    assert player.calls == [("popup", "One.", -1, "specialk"), ("chat", "Two.", "specialk")]
    assert [call[0] for call in recording_bot.calls] == [
        "Popup sent to Fragmagnet on server 'SRS-1'.",
        "Chat message sent to Fragmagnet on server 'SRS-1'.",
        "Message must not be empty.",
    ]


@pytest.mark.asyncio
async def test_a_failed_audit_is_reported_on_the_page_and_never_fails_the_write(config_dir,
                                                                              clean_registry):
    """A success whose trail could not be written says so -- and stays a success."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    class FailingBot:
        async def audit(self, message, **kwargs):
            raise RuntimeError("the audit channel is unreachable")

    cluster = message_fleet()
    app = build(config_dir, cluster)

    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=FailingBot())
    try:
        async with client(app) as http:
            await sign_in(http, "mod1")
            html = (await http.get(ROSTER)).text
            reply = await send(http, POPUP, token_of(html), "Hi.")
            assert reply.status_code == 303
            after = (await http.get(ROSTER)).text
            assert "Popup sent to Fragmagnet" in after
            assert "could not be recorded" in after
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


# --------------------------------------------------------------------------- 8. the deploy pin

def test_the_console_and_the_action_agree_on_the_field_length():
    """The console's control cap and the ACTION's own limit are ONE number, pinned to each other.

    ``pages/actions.py`` may not import the plugin (a console whose plugin is absent still renders
    its pages), so the two constants are declared separately and could drift; the drift would be
    silent in both directions — a form the action refuses, or a limit the form never enforces.
    """
    from plugins.mission.actions import MAX_MESSAGE_LENGTH

    assert actions_page.MESSAGE_FIELD_MAX == MAX_MESSAGE_LENGTH


def test_the_action_module_is_importable_without_a_running_bot():
    """The action is a plain module-level function: no bot, no service, no web import needed.

    It matters to the DEPLOY list (the report's second half): an existing installation must receive
    ``plugins/mission/actions.py`` for the console's Message control to exist at all, and this test
    is what says that file carries no runtime dependency the install does not already have.
    """
    import inspect

    from plugins.mission.actions import message_player

    assert message_player.__qualname__ == "message_player"
    source = inspect.getsource(message_player)
    assert "services.webservice" not in source, "the action reaches into the web layer"
