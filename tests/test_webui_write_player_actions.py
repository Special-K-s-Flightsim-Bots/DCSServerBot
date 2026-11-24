"""The player row strip: kick / ban / chat / popup / mute, the ban dialog, the refusal audit.

One section per acceptance criterion: the DISCOVERY WIRING; the ACTION LAYER (an empty/whitespace kick
or ban reason does NOT reach the domain call; the ban duration maps empty -> permanent, N -> N and
nonsense -> a TYPED refusal with nothing written; mute/unmute reach the right method; "no such player"
is a typed refusal); the STRIP (five glyphs on a player row, each with a native ``title`` AND an
``aria-label`` naming the player, built from ONE component shared with the server row; a muted player
gets Unmute); the CAPABILITY and the SCOPE (a ``DCS`` member is offered nothing and refused, a manager
only their own servers, and a crafted POST for another server's player is refused indistinguishably
from a nonexistent server); the CONFIRM STEP (kick, ban and the two message controls refuse a POST
that skipped their dialog; mute needs no dialog); CSRF; and the AUDIT (kick and ban record success,
refusal AND refusal-by-permission/scope, while chat/popup/mute deliberately do not). Driven through the
real app and a stub cluster."""
from __future__ import annotations

import re
import types
from pathlib import Path

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


def _actions():
    """The ``mission`` plugin's action module, imported INSIDE a test -- never at collection time.

    A module-level import would run its ``@action`` decorators while pytest is still COLLECTING,
    populating the process-wide registry before a single test runs, so every console test that builds
    an install WITHOUT the plugin would render an Actions column it must not have. Importing here keeps
    the registry's state the fixture's business.
    """
    import importlib

    return importlib.import_module("plugins.mission.actions")

# ONE harness for the console's write actions: the pause pilot's and message doubles are reused, not
# copied. ``config_dir``, ``clean_registry`` and ``recording_bot`` are FIXTURES and are imported as
# such — a pytest fixture found in the module's namespace is a fixture of this module.
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    Cluster, build, clean_registry, client, config_dir, instance, recording_bot,
    server as server_double, sign_in, token_of,
)
from test_webui_write_message import (  # noqa: F401  (the helpers are used by name)
    UCID_A, UCID_B, forms_for, notified,
)


@pytest.fixture(autouse=True)
def _isolated_registry(clean_registry):  # noqa: F811 - the harness fixture, made autouse here
    """Every test in THIS file runs with the console suites' registry isolation.

    The action-layer tests below import ``plugins.mission.actions`` themselves (lazily — see
    :func:`_actions`), and that import populates a PROCESS-WIDE registry through the module's
    decorators. Without this fixture the last such test would leave the registry populated for every
    file that runs afterwards, which is exactly the cross-file leak the eager import caused.
    """
    return clean_registry

KICK = "/actions/player/kick"
BAN = "/actions/player/ban"
MUTE = "/actions/player/mute"
UNMUTE = "/actions/player/unmute"
CHAT = actions_page.CHAT_PATH
POPUP = actions_page.POPUP_PATH

#: the dialog suffix, built from the constant so a rename cannot leave a test pointing at a route
#: that no longer exists
CONFIRM = actions_page.CONFIRM_SUFFIX

SENDER = "mod1"


class FakePlayer:
    """The ``Player`` double: the four methods, awaiting and recording, and forwarding to DCS.

    ``muted`` is a plain bool because that is all the action reads; ``mute()``/``unmute()`` flip it as
    ``core/data/player.py`` does.
    """

    def __init__(self, name, ucid, *, active=True, muted=False):
        self.name = name
        self.display_name = name
        self.ucid = ucid
        self.active = active
        self.muted = muted
        self.unit_display_name = "A-10C II"
        self.group_name = "Hog 1"
        self.id = 101
        self.unit_name = "unit-101"
        self.server = None  # bound by player_server
        self.calls: list[str] = []
        self.messages: list[tuple] = []

    async def mute(self):
        self.calls.append("mute")
        self.muted = True

    async def unmute(self):
        self.calls.append("unmute")
        self.muted = False

    async def sendPopupMessage(self, message, timeout=-1, sender=None):
        self.messages.append(("popup", message, timeout, sender))

    async def sendChatMessage(self, message, sender=None):
        self.messages.append(("chat", message, sender))


def _lookup(server, ucid, active):
    """``Server.get_player``'s UCID branch, which is the only one these actions use."""
    player = server.players.get(ucid)
    if player is None or (active is not None and player.active != active):
        return None
    return player


def player_server(name, managed_by=None, *, players=(), status=Status.RUNNING,
                  node_name="dcs-a-01", instance_name="Host-A-Instance"):
    """The pause pilot's server double plus what a PLAYER write needs: ``players``, ``get_player``
    and a recording ``kick`` (the domain method ``kick_player`` calls)."""
    srv = server_double(name, managed_by, status=status, node_name=node_name,
                        instance=instance_name)
    srv.players = {player.ucid: player for player in players}
    for player in players:
        player.server = srv
    srv.kicks = []
    srv.dcs_calls = []

    async def kick(player, reason="n/a"):
        srv.kicks.append((player.ucid, reason))

    async def send_to_dcs(payload):
        srv.dcs_calls.append(payload)

    srv.kick = kick
    srv.send_to_dcs = send_to_dcs
    srv.get_player = lambda ucid=None, active=None: _lookup(srv, ucid, active)
    return srv


class FakeBanService:
    """The ``ServiceBus`` double: ``ban`` records the arguments the action passed it.

    ``server_name`` is the ban's scope — the servers the CALLER manages — and is recorded with the
    rest so a route test can pin WHICH rows the ban wrote.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.servers: dict = {}

    async def ban(self, ucid, banned_by, reason="n/a", days=None, server_name=None, banned_at=None):
        self.calls.append((ucid, banned_by, reason, days, server_name))


@pytest.fixture
def ban_service():
    """Install a bus that CAN record bans, for the process's whole request handling.

    The web caller is handed the console's scoped ``_Cluster`` as ``ctx.bus``, which deliberately
    carries no ``ban`` — so ``ban_player`` resolves the real service from the registry, exactly as
    ``BanModal`` does. This is what lets the ROUTE-level test assert the ban reached it.
    """
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    bus = FakeBanService()
    saved = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = bus
    try:
        yield bus
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


def player_fleet(*, a_muted=False, a_players=None, b_players=None):
    """A (Host A) and B (Host B), each with one ACTIVE player — the scope's two sides."""
    a = player_server("SRS-1", ["Host A"],
                      players=a_players if a_players is not None
                      else [FakePlayer("Fragmagnet", UCID_A, muted=a_muted)])
    b = player_server("SRS-B", ["Host B"],
                      players=b_players if b_players is not None
                      else [FakePlayer("Viper", UCID_B)],
                      node_name="dcs-b-01", instance_name="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


def direct_ctx(servers, ban_service=None):
    """An ``ActionContext`` with no scope stated: the ACTION is driven directly (as MCP would).

    ``ban`` is attached to the SAME object as ``servers`` on purpose: ``core/actions`` hands the
    action ONE bus, and ``ban_player`` resolves the ban list from it when it carries one (the object
    whose ``.ban`` is callable) — so the double must be that object, not a wrapper around one.
    """
    bus = ban_service if ban_service is not None else types.SimpleNamespace()
    bus.servers = dict(servers)
    return ActionContext(node=types.SimpleNamespace(name="NODE"), bus=bus)


def offered(html: str) -> set[str]:
    """The control paths the page actually RENDERS, for a player row."""
    return set(re.findall(r'action="(/actions/player/[^"]+)"', html))


async def open_players(http):
    response = await http.get(ROSTER)
    assert response.status_code == 200, response.text
    return response.text


def confirm_of(dialog: str) -> str:
    """The one-shot token a rendered dialog's form carries."""
    match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog)
    assert match, "the dialog rendered no confirm token"
    return match.group(1)


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_player_actions_in_its_own_process(config_dir,
                                                                            clean_registry):
    """``action_available`` for every action the player strip wires, through the shell's install.

    The three actions are ``plugins/mission/actions.py``'s: without the shell's discovery they are not
    in the registry and this file's other assertions would pass over a strip that renders nothing.
    """
    for qualname in ("kick_player", "ban_player", "mute_player", "unmute_player"):
        assert action_available(qualname) is False, "the registry must start EMPTY for this test"
    build(config_dir, player_fleet())
    for qualname in ("kick_player", "ban_player", "mute_player", "unmute_player"):
        assert action_available(qualname) is True, qualname
        assert qualname in action_registry()


# --------------------------------------------------------------- 2. the action layer (directly)

@pytest.mark.asyncio
async def test_an_empty_or_whitespace_kick_reason_never_reaches_the_domain_call():
    """ACCEPTANCE 1a — a blank reason is refused WITHOUT calling ``Server.kick``."""
    from plugins.mission.actions import MAX_REASON_LENGTH, kick_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    ctx = direct_ctx({srv.name: srv})
    for blank in ("", "   ", "\t\n", None):
        result = await kick_player(ctx, "SRS-1", UCID_A, blank)
        assert isinstance(result, PlayerActionResult)
        assert result.success is False, f"{blank!r} was accepted"
        assert result.message == "A reason is required to kick a player."
        assert srv.kicks == [], f"{blank!r} reached the domain call"
    too_long = await kick_player(ctx, "SRS-1", UCID_A, "x" * (MAX_REASON_LENGTH + 1))
    assert too_long.success is False
    assert str(MAX_REASON_LENGTH) in too_long.message
    assert srv.kicks == []


@pytest.mark.asyncio
async def test_the_kick_reaches_the_domain_call_with_the_trimmed_reason():
    """The positive control: a real reason DOES reach ``Server.kick(player, reason)``."""
    from plugins.mission.actions import kick_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    ctx = direct_ctx({srv.name: srv})
    result = await kick_player(ctx, "SRS-1", UCID_A, "  teamkilling on the runway  ")
    assert result.success is True
    assert (result.player_name, result.ucid) == ("Fragmagnet", UCID_A)
    assert srv.kicks == [(UCID_A, "teamkilling on the runway")], "the reason must be trimmed"


@pytest.mark.asyncio
async def test_a_player_who_is_not_on_the_resolved_server_is_a_typed_refusal():
    """The player is looked up ON the resolved server — there is no lookup that begins elsewhere."""
    from plugins.mission.actions import kick_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    ctx = direct_ctx({srv.name: srv})
    result = await kick_player(ctx, "SRS-1", UCID_B, "reason")
    assert result.success is False
    assert result.message == "Player not found on server 'SRS-1'."
    assert srv.kicks == []


@pytest.mark.asyncio
async def test_the_ban_duration_maps_empty_to_permanent_n_to_n_and_nonsense_to_a_refusal(
        ban_service):
    """ACCEPTANCE 1b — the three duration answers, asserted on the ban the service really received."""
    from plugins.mission.actions import MAX_BAN_DAYS, ban_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    bus = FakeBanService()
    ctx = direct_ctx({srv.name: srv}, ban_service=bus)

 # empty / absent -> permanent (None), exactly what the Discord modal does with an empty field
    for blank in ("", "   ", None):
        result = await ban_player(ctx, "SRS-1", UCID_A, "cheating", blank)
        assert result.success is True, result.message
        assert "permanently" in result.message
    assert bus.calls == [(UCID_A, "", "cheating", None, None)] * 3
    bus.calls.clear()

 # N days -> N
    days = await ban_player(ctx, "SRS-1", UCID_A, "cheating", "7")
    assert days.success is True and "for 7 days" in days.message
    assert bus.calls == [(UCID_A, "", "cheating", 7, None)]
    bus.calls.clear()

 # nonsense -> a TYPED refusal and NOTHING written
    for bad in ("-3", "7.5", "abc", "0", "  ", str(MAX_BAN_DAYS + 1)):
        if bad == "  ":
            continue        # whitespace is the permanent case above, not a refusal
        result = await ban_player(ctx, "SRS-1", UCID_A, "cheating", bad)
        assert result.success is False, f"{bad!r} was accepted"
        assert result.message.startswith("Days must"), result.message
    assert bus.calls == [], "a malformed duration must not reach the ban list"


def test_parse_ban_days_is_one_implementation_with_a_stated_answer_per_input():
    """The parser's own table, pinned so the route, the action and the dialog cannot drift."""
    from plugins.mission.actions import MAX_BAN_DAYS, parse_ban_days

    assert parse_ban_days("") == (None, "")
    assert parse_ban_days("   ") == (None, "")
    assert parse_ban_days(None) == (None, "")
    assert parse_ban_days(7) == (7, "")
    assert parse_ban_days(" 30 ") == (30, "")
    assert parse_ban_days(str(MAX_BAN_DAYS)) == (MAX_BAN_DAYS, "")
    for bad in ("-1", "0", "1.5", "seven", str(MAX_BAN_DAYS + 1)):
        days, refusal = parse_ban_days(bad)
        assert days is None and refusal, bad


@pytest.mark.asyncio
async def test_an_empty_ban_reason_never_reaches_the_ban_list(ban_service):
    """ACCEPTANCE 1a for the ban — and the over-long reason the Discord modal's field cannot send."""
    from plugins.mission.actions import MAX_BAN_REASON_LENGTH, ban_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    bus = FakeBanService()
    ctx = direct_ctx({srv.name: srv}, ban_service=bus)
    for blank in ("", "   ", None):
        result = await ban_player(ctx, "SRS-1", UCID_A, blank, "7")
        assert result.success is False
        assert result.message == "A reason is required to ban a player."
    long = await ban_player(ctx, "SRS-1", UCID_A, "x" * (MAX_BAN_REASON_LENGTH + 1), "7")
    assert long.success is False and str(MAX_BAN_REASON_LENGTH) in long.message
    assert bus.calls == []


@pytest.mark.asyncio
async def test_the_ban_reaches_the_service_with_the_identity_s_own_name_as_banned_by(ban_service):
    """``banned_by`` is the SENDER the route passed (the identity's shown name), never a form field."""
    from plugins.mission.actions import ban_player

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    bus = FakeBanService()
    ctx = direct_ctx({srv.name: srv}, ban_service=bus)
    result = await ban_player(ctx, "SRS-1", UCID_A, "cheating", "", sender=SENDER)
    assert result.success is True
    assert bus.calls == [(UCID_A, SENDER, "cheating", None, None)]


@pytest.mark.asyncio
async def test_a_ban_with_no_ban_list_is_a_typed_refusal(ban_service):
    """The console's own concern, not an AttributeError: ``ctx.bus`` is the scoped cluster by design.

    ``ban_service`` is installed (the real registry path), so this drives the OTHER branch: an
    action whose bus carries no ``ban`` and a registry that has none either.
    """
    from core.services.registry import ServiceRegistry
    from plugins.mission.actions import ban_player
    from services.servicebus import ServiceBus

    srv = player_server("SRS-1", ["Host A"], players=[FakePlayer("Fragmagnet", UCID_A)])
    saved = ServiceRegistry._singletons.pop(ServiceBus, None)
    try:
        ctx = direct_ctx({srv.name: srv})
        result = await ban_player(ctx, "SRS-1", UCID_A, "cheating", "")
        assert result.success is False
        assert result.message == "The ban list is not available in this installation."
    finally:
        if saved is not None:
            ServiceRegistry._singletons[ServiceBus] = saved


@pytest.mark.asyncio
async def test_mute_and_unmute_reach_the_right_method_and_refuse_a_no_op():
    """ACCEPTANCE 3 — the right method, and a repeated click is a typed refusal, not a second call."""
    from plugins.mission.actions import mute_player, unmute_player

    player = FakePlayer("Fragmagnet", UCID_A)
    srv = player_server("SRS-1", ["Host A"], players=[player])
    ctx = direct_ctx({srv.name: srv})

    muted = await mute_player(ctx, "SRS-1", UCID_A)
    assert muted.success is True and player.calls == ["mute"] and player.muted is True
    again = await mute_player(ctx, "SRS-1", UCID_A)
    assert again.success is False and "already muted" in again.message
    assert player.calls == ["mute"], "a no-op must not repeat the DCS call"

    unmuted = await unmute_player(ctx, "SRS-1", UCID_A)
    assert unmuted.success is True and player.calls == ["mute", "unmute"] and player.muted is False
    assert (await unmute_player(ctx, "SRS-1", UCID_A)).success is False


# ------------------------------------------------------------------------------- 3. the strip

@pytest.mark.asyncio
async def test_a_player_row_carries_the_five_glyphs_with_both_names(config_dir, clean_registry):
    """kick · ban · chat · popup · mute, each a glyph with a ``title`` AND an ``aria-label`` naming the
    target, on the dashboard's Players tab and on /players."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
        tab = await http.get("/?tab=players")
    assert tab.status_code == 200
    for html in (page, tab.text):
        assert KICK + CONFIRM in offered(html), "the kick control posts to its dialog"
        assert BAN + CONFIRM in offered(html)
        assert MUTE in offered(html), "an unmuted player is offered Mute"
        assert CHAT + CONFIRM in offered(html), "chat is its own control, posting to its dialog"
        assert POPUP + CONFIRM in offered(html), "popup is its own control, posting to its dialog"
        assert "act-strip" in html
        assert 'class="act-btn more"' not in html, "a player row has no overflow trigger"

 # the two names, per control, naming the PLAYER (the target) and the server
    for title, aria in (
        ("Kick — disconnected now; they may rejoin immediately",
         "Kick Fragmagnet from SRS-1"),
        ("Ban — removed and blocked from rejoining", "Ban Fragmagnet from SRS-1"),
        ("Chat — a chat line only this player reads",
         "Send a chat line to Fragmagnet on SRS-1"),
        ("Popup — a private message in game", "Send a popup to Fragmagnet on SRS-1"),
        ("Mute — they can no longer send chat", "Mute Fragmagnet on SRS-1"),
    ):
        assert f'title="{title}"' in page, title
        assert f'aria-label="{aria}"' in page, aria

 # ONE form per message control (two controls, one per mode), and NEITHER the text field nor the
 # mode button is on the row: the message is typed in the dialog the glyph opens
    for path in (CHAT, POPUP):
        forms = forms_for(page, path + CONFIRM)
        assert len(forms) == 1, f"one control per mode: {path} rendered {len(forms)} forms"
        form = forms[0]
        assert f'value="{UCID_A}"' in form
        assert 'name="message"' not in form, "the message field belongs to the dialog"
        assert 'name="mode"' not in form, "the mode is the control's own, not a submit button"


@pytest.mark.asyncio
async def test_the_mute_control_reflects_the_player_s_current_state(config_dir, clean_registry):
    """ACCEPTANCE 3 — a MUTED player is offered Unmute and never both."""
    app = build(config_dir, player_fleet(a_muted=True))
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
    assert UNMUTE in offered(page), "a muted player must be offered Unmute"
    assert MUTE not in offered(page), "both mute controls on one row is the bug this pins"
    assert 'aria-label="Unmute Fragmagnet on SRS-1"' in page
    assert "act-strip" in page


def test_every_player_control_points_at_a_glyph_that_exists():
    """No control may name a glyph the partial does not draw: the alternative is an EMPTY ``<svg>``
    inside a perfectly normal-looking button, which no other test would notice.

    ONE glyph per control, now that a control is a single button: ``chat`` and ``popup`` are two
    controls and carry their own."""
    source = Path(actions_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z]+)':\s*'", source.read_text(encoding="utf-8")))
    missing = sorted({action.icon for action in actions_page.PLAYER_ACTIONS if action.icon} -
                     glyphs)
    assert missing == [], f"control(s) with no glyph: {missing}"
    assert sorted(a.key for a in actions_page.PLAYER_ACTIONS) == \
        ["ban", "chat", "kick", "mute", "popup", "screenshot", "unmute"], \
        "the row's control set moved"


# ------------------------------------------------------------ 4. capability and scope

@pytest.mark.asyncio
async def test_a_dcs_member_is_offered_no_player_control_and_cannot_post(config_dir, clean_registry,
                                                                        recording_bot, ban_service):
    """A ``DCS`` member: no console at all, no control in any markup, and the gate refuses first."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "plain")
        page = await http.get(ROSTER)
        assert page.status_code == 403
        assert "act-strip" not in page.text and "act-btn" not in page.text
        reply = await http.post(MUTE, data={"server": "SRS-1", "ucid": UCID_A,
                                            "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"
        assert "Internal Server Error" not in reply.text
    assert cluster.servers[0].players[UCID_A].calls == []
    assert ban_service.calls == []
    assert recording_bot.calls == [], "a capability refusal never reaches a route, so it audits nothing"


@pytest.mark.asyncio
async def test_a_manager_gets_controls_only_on_their_own_servers_players(config_dir, clean_registry):
    """The scope, on the strip: their own row keeps its glyphs, somebody else's row has none."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")             # manages Host B only
        page = await open_players(http)
    assert f'value="{UCID_B}"' in page, "their own player keeps its controls"
    assert f'value="{UCID_A}"' not in page, "a control was built for somebody else's player"


@pytest.mark.asyncio
async def test_a_crafted_post_for_another_servers_player_is_refused_indistinguishably(
        config_dir, clean_registry, recording_bot, ban_service):
    """The scope, on the route: the same bare 403 for a real out-of-scope name and for a name that
    does not exist — asserted on a non-confirm action (mute), where the refusal is reachable without
    a dialog token, and nothing runs."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        page = await open_players(http)
        token = token_of(page)
        foreign = await http.post(MUTE, data={"server": "SRS-1", "ucid": UCID_A,
                                              "_csrf_token": token})
        missing = await http.post(MUTE, data={"server": "NOPE-9", "ucid": UCID_A,
                                              "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text, "the two refusals must be indistinguishable"
    assert "SRS-1" not in foreign.text
    assert cluster.servers[0].players[UCID_A].calls == []
 # a refusal the route makes for mute leaves NO audit entry — the trail records the ACTION's own
 # refusals there, and only the DESTRUCTIVE pair (kick, ban) audits a refused-permission POST.
    assert recording_bot.calls == [], "only kick and ban audit a route-level refusal"


# ------------------------------------------------------------------------ 5. the confirm step

@pytest.mark.asyncio
async def test_kick_refuses_a_post_that_skipped_its_dialog_and_runs_after_it(config_dir,
                                                                            clean_registry,
                                                                            recording_bot,
                                                                            ban_service):
    """THE rule: a confirmation curl can skip is decoration. And the DIALOG carries the reason."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    srv = cluster.servers[0]
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
        token = token_of(page)

        direct = await http.post(KICK, data={"server": "SRS-1", "ucid": UCID_A, "reason": "x",
                                             "_csrf_token": token})
        assert direct.status_code == 403, "a POST that never opened the dialog must be refused"
        assert srv.kicks == []

        dialog = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                       "_csrf_token": token})
        assert dialog.status_code == 200
        assert "<b>Fragmagnet is disconnected from SRS-1 now.</b>" in dialog.text
        assert "They can rejoin immediately" in dialog.text
        assert 'name="reason"' in dialog.text, "the reason field belongs to the dialog"
        assert 'name="ucid"' in dialog.text and f'value="{UCID_A}"' in dialog.text
        assert "Days" not in dialog.text, "a kick has no duration"
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
        assert confirm, "the dialog carries no confirm token"

        accepted = await http.post(KICK, data={"server": "SRS-1", "ucid": UCID_A,
                                               "reason": "teamkilling",
                                               "_csrf_token": token,
                                               "_confirm_token": confirm.group(1)})
        assert accepted.status_code == 303
        assert srv.kicks == [(UCID_A, "teamkilling")]

        replay = await http.post(KICK, data={"server": "SRS-1", "ucid": UCID_A,
                                             "reason": "teamkilling", "_csrf_token": token,
                                             "_confirm_token": confirm.group(1)})
        assert replay.status_code == 403, "the token is SPENT"
    assert srv.kicks == [(UCID_A, "teamkilling")]


@pytest.mark.asyncio
async def test_the_ban_dialog_carries_the_days_field_and_a_blank_one_is_permanent(
        config_dir, clean_registry, recording_bot, ban_service):
    """ACCEPTANCE 1b through the ROUTE: the dialog asks for the duration, and blank means permanent."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
        token = token_of(page)
        dialog = await http.post(BAN + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                      "_csrf_token": token})
        assert dialog.status_code == 200
        assert "<b>Fragmagnet is removed from SRS-1 and cannot rejoin it</b>" in dialog.text
        assert "Empty duration means it never expires on its own." in dialog.text
        assert "Days (optional)" in dialog.text and 'name="days"' in dialog.text
        assert "Empty = permanent" in dialog.text, "the field says what blank means"
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)

        accepted = await http.post(BAN, data={"server": "SRS-1", "ucid": UCID_A,
                                              "reason": "cheating", "days": "",
                                              "_csrf_token": token, "_confirm_token": confirm})
        assert accepted.status_code == 303
        after = await open_players(http)
        assert notified(after, "banned permanently"), after
    assert ban_service.calls == [(UCID_A, SENDER, "cheating", None, "SRS-1")]


@pytest.mark.asyncio
async def test_the_ban_route_refuses_nonsense_duration_with_a_typed_refusal_not_a_500(
        config_dir, clean_registry, recording_bot, ban_service):
    """The dialog's field is attacker-shaped: a nonsense duration must surface as copy, not a 500."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
        token = token_of(page)
        dialog = await http.post(BAN + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                      "_csrf_token": token})
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(BAN, data={"server": "SRS-1", "ucid": UCID_A, "reason": "cheating",
                                           "days": "-3", "_csrf_token": token,
                                           "_confirm_token": confirm})
        assert reply.status_code == 303, reply.text
        after = await open_players(http)
        assert notified(after, "Days must be a whole number"), after
        assert "Internal Server Error" not in after
    assert ban_service.calls == [], "a refused duration wrote nothing to the ban list"


@pytest.mark.asyncio
async def test_the_player_dialog_refuses_a_foreign_server_like_a_missing_one(config_dir,
                                                                            clean_registry,
                                                                            recording_bot,
                                                                            ban_service):
    """The dialog answers the SAME refusals as its POST."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        page = await open_players(http)
        token = token_of(page)
        foreign = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                        "_csrf_token": token})
        missing = await http.post(KICK + CONFIRM, data={"server": "NOPE-9", "ucid": UCID_A,
                                                        "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text
    assert "SRS-1" not in foreign.text


@pytest.mark.asyncio
async def test_chat_popup_and_mute_reach_their_write_the_way_their_declaration_says(config_dir,
                                                                                    clean_registry,
                                                                                    recording_bot,
                                                                                    ban_service):
    """The modes of the two message controls are their OWN (``constants``), and the write is reached
    through the dialog the row opens; ``mute`` is the plain direct POST it always was."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]
    by_key = {action.key: action for action in actions_page.PLAYER_ACTIONS}
    assert by_key["chat"].constants == (("mode", "chat"),)
    assert by_key["popup"].constants == (("mode", "popup"),)
    assert by_key["mute"].constants == () and by_key["mute"].confirm is False
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        popup_dialog = await http.post(POPUP + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                              "_csrf_token": token})
        popup = await http.post(POPUP, data={"server": "SRS-1", "ucid": UCID_A,
                                             "message": "Check your six.",
                                             "_csrf_token": token,
                                             "_confirm_token": confirm_of(popup_dialog.text)})
        chat_dialog = await http.post(CHAT + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                            "_csrf_token": token})
        chat = await http.post(CHAT, data={"server": "SRS-1", "ucid": UCID_A,
                                           "message": "Look alive.", "_csrf_token": token,
                                           "_confirm_token": confirm_of(chat_dialog.text)})
        muted = await http.post(MUTE, data={"server": "SRS-1", "ucid": UCID_A,
                                            "_csrf_token": token})
    assert popup.status_code == 303 and chat.status_code == 303 and muted.status_code == 303
    assert player.calls == ["mute"]
    assert [call[0] for call in recording_bot.calls] == [
        "Popup sent to Fragmagnet on server 'SRS-1'.",
        "Chat message sent to Fragmagnet on server 'SRS-1'.",
        "Player Fragmagnet on server 'SRS-1' muted."]


# ------------------------------------------------------------------------------- 6. the CSRF

@pytest.mark.asyncio
async def test_the_new_player_routes_refuse_a_post_with_no_session_token(config_dir, clean_registry,
                                                                        recording_bot, ban_service):
    """Every new route carries the session's CSRF dependency, the dialog loaders included."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    browser = {"Accept": "text/html"}
    async with client(app) as http:
        await sign_in(http, "mod1")
        for path in (KICK, KICK + CONFIRM, BAN, BAN + CONFIRM, MUTE, UNMUTE,
                     CHAT, CHAT + CONFIRM, POPUP, POPUP + CONFIRM):
            reply = await http.post(path, data={"server": "SRS-1", "ucid": UCID_A},
                                    headers=browser)
            assert reply.status_code == 403, path
            assert "nothing was saved" in reply.text, path
    assert cluster.servers[0].kicks == []
    assert cluster.servers[0].players[UCID_A].calls == []
    assert ban_service.calls == []
    assert recording_bot.calls == []


# ------------------------------------------------------------------------------ 7. the audit

@pytest.mark.asyncio
async def test_the_audit_records_the_kick_success_and_the_action_s_own_refusal(config_dir,
                                                                               clean_registry,
                                                                               recording_bot,
                                                                               ban_service):
    """One entry per ATTEMPT, naming the web actor — the success and the typed refusal."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    srv = cluster.servers[0]
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        dialog = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                       "_csrf_token": token})
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        first = await http.post(KICK, data={"server": "SRS-1", "ucid": UCID_A,
                                            "reason": "teamkilling", "_csrf_token": token,
                                            "_confirm_token": confirm})
        assert first.status_code == 303
 # the player's ucid is NOT on this server any more: the second attempt meets the lookup guard
        token = token_of(await open_players(http))
        dialog = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                       "_csrf_token": token})
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        second = await http.post(KICK, data={"server": "SRS-1", "ucid": UCID_A,
                                             "reason": "", "_csrf_token": token,
                                             "_confirm_token": confirm})
        assert second.status_code == 303
    assert srv.kicks == [(UCID_A, "teamkilling")], "the refused attempt ran no domain call"
    messages = [call[0] for call in recording_bot.calls]
    assert messages == [
        "Player Fragmagnet (ucid=" + UCID_A + ") kicked from server 'SRS-1'.",
        "A reason is required to kick a player.",
    ]


@pytest.mark.asyncio
async def test_the_audit_records_a_refused_permission_post_for_the_destructive_pair(config_dir,
                                                                                    clean_registry,
                                                                                    recording_bot,
                                                                                    ban_service):
    """For kick and ban ONLY.

    A manager POSTs at a server outside their scope: the response is the console's bare 403 (the same
    one a missing server gets) AND the attempt is recorded, naming the actor, the action and the target.
    The same POST for MUTE leaves no entry, because a refusal there is not an incident.
    """
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")             # manages Host B only
        token = token_of(await open_players(http))
        kick = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                     "_csrf_token": token})
        ban = await http.post(BAN + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                   "_csrf_token": token})
        mute = await http.post(MUTE, data={"server": "SRS-1", "ucid": UCID_A,
                                           "_csrf_token": token})
    assert kick.status_code == 403 and ban.status_code == 403 and mute.status_code == 403
    messages = [call[0] for call in recording_bot.calls]
    assert messages == [
        "refused kick_player: not permitted (outside your scope) "
        "(server 'SRS-1', ucid=" + UCID_A + ")",
        "refused ban_player: not permitted (outside your scope) "
        "(server 'SRS-1', ucid=" + UCID_A + ")",
    ], "kick and ban record the refused attempt; mute deliberately does not"


@pytest.mark.asyncio
async def test_the_ban_audit_names_the_actor_and_the_outcome(config_dir, clean_registry,
                                                            recording_bot, ban_service):
    """The ban's own trail, one entry, naming the web actor and what it did."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        dialog = await http.post(BAN + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                      "_csrf_token": token})
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(BAN, data={"server": "SRS-1", "ucid": UCID_A, "reason": "cheating",
                                           "days": "7", "_csrf_token": token,
                                           "_confirm_token": confirm})
        assert reply.status_code == 303
    assert ban_service.calls == [(UCID_A, SENDER, "cheating", 7, "SRS-1")]
    assert [call[0] for call in recording_bot.calls] == [
        "Player Fragmagnet (ucid=" + UCID_A + ") banned for 7 days on SRS-1."]


@pytest.mark.asyncio
async def test_a_route_refusal_for_a_missing_server_is_audited_for_the_direct_player_write(
        config_dir, clean_registry, recording_bot, ban_service):
    """The not-found refusal is the ROUTE's. It is reached WITHOUT a dialog by the one direct write
    (``mute``), which audits it; the two that do not audit refusals leave one entry for the ACTION
    only. For the four dialog-backed controls the same branch is the honest answer to a server that
    vanished between the dialog and the POST — a POST that skips the dialog never reaches it, so
    what a caller meets for a name that does not exist there is the DIALOG LOADER's own answer.

    An UNSCOPED caller is used on purpose: a restricted scope answers NOT_PERMITTED for a name it
    cannot see, so only an unscoped caller reaches the honest "no such server" branch.
    """
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_players(http))
        mute = await http.post(MUTE, data={"server": "NOPE-9", "ucid": UCID_A,
                                           "_csrf_token": token})
        assert mute.status_code == 303, "the route's own refusal is a rendered banner"
        for path in (CHAT, POPUP):
            loader = await http.post(path + CONFIRM, data={"server": "NOPE-9", "ucid": UCID_A,
                                                           "_csrf_token": token})
            assert loader.status_code == 404, f"{path}: the dialog loader names the missing server"
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'NOPE-9' not found.",
    ]


# ------------------------------------------------------------- 8. one component, both screens

@pytest.mark.asyncio
async def test_the_player_rows_use_the_same_strip_component_as_the_server_rows(config_dir,
                                                                               clean_registry):
    """The row is ONE component: the server, node, player, BANS and MODS strips are the SAME
    partial, and a player control is a glyph button exactly like a server control.

    RESTATED: the node row now renders its lifecycle trio through this same partial, the Bans row its
    edit/lift pair, the Mods row its change-version/remove pair and the player-directory row its
    ban/watch pair, so the include count is six (server, node, player, bans, mods, directory) rather
    than two. The assertion still pins "one component for every row", which is the property worth
    keeping — it is the COUNT that moved, not the property.

    THE ROWS LIVE IN A PARTIAL OF THEIR OWN (``_table_rows.html``) because the Servers page's body is
    a live swap target: the count is read THERE, and ``_table.html`` is asserted to be the one place
    that includes it — so the rows a page draws and the rows a streamed frame carries are one file.
    """
    templates = Path(actions_page.__file__).parent.parent.joinpath("templates")
    markup = templates.joinpath("_table.html").read_text(encoding="utf-8")
    assert '{% include "_table_rows.html" %}' in markup, \
        "the table component draws its rows from the one rows partial"
    rows = templates.joinpath("_table_rows.html").read_text(encoding="utf-8")
    assert rows.count('{% include "_strip.html" %}') == 6, \
        "the server, node, player, bans, mods and directory rows must all render the SAME partial"
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
    for button in re.findall(r'<button class="act-btn[^"]*"[^>]*>(.*?)</button>', page, re.S):
        assert not re.search(r">[A-Za-z][^<]*<", button), f"a visible label in the strip: {button}"


# ------------------------------------------- the fields' SHAPE: a small count, a wide reason

@pytest.mark.asyncio
async def test_the_ban_duration_is_a_small_bounded_number_box(config_dir, clean_registry,
                                                             recording_bot, ban_service):
    """ACCEPTANCE — the duration is a NUMBER box bounded 1..999 and three characters wide.

    It was a text field of the shared 300-pixel width, which invited a date or a paragraph into a
    field that holds a day count. The browser now refuses to submit outside the range; the ACTION
    keeps its own, wider guard (``MAX_BAN_DAYS``) because the field is a shape, never the authority.
    """
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        dialog = await http.post(BAN + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                      "_csrf_token": token})
        assert dialog.status_code == 200, dialog.text
        text = dialog.text

    assert '<p class="dlg-field narrow">' in text, "the duration box is not the narrow field"
    match = re.search(r'<input type="number"[^>]*name="days"[^>]*>', text)
    assert match, "the duration is not a number input"
    tag = match.group(0)
    assert f'min="{actions_page.BAN_DAYS_MIN}"' in tag, tag
    assert f'max="{actions_page.BAN_DAYS_MAX}"' in tag, tag
    assert 'step="1"' in tag and 'maxlength="3"' in tag, tag
    assert actions_page.BAN_DAYS_MAX < _actions().MAX_BAN_DAYS, \
        "the console's bound must stay inside the action's own"
    assert '<p class="dlg-field wide">' in text, "the reason is not the wide field"


@pytest.mark.asyncio
async def test_the_kick_reason_is_the_wide_field(config_dir, clean_registry):
    """The kick reason takes the dialog's full width — it holds up to 200 characters."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        dialog = await http.post(KICK + CONFIRM, data={"server": "SRS-1", "ucid": UCID_A,
                                                       "_csrf_token": token})
        assert dialog.status_code == 200, dialog.text
        text = dialog.text

    assert '<p class="dlg-field wide">' in text, "the kick reason is not the wide field"
    assert 'name="reason"' in text
