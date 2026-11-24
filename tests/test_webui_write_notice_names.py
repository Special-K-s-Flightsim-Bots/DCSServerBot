"""The notice a write leaves behind: the names in it are the PLAIN ones.

Every domain object carries its name twice: ``name`` is the plain string and ``display_name`` is that
same string escaped for Discord's markdown (``core/data/player.py`` / ``core/data/server.py``:
``utils.escape_string``), so a player called ``[TAG]_Test*Pilot`` reads back as ``\\[TAG\\]\\_Test\\*Pilot``
and the server ``SRS-1`` as ``SRS\\-1``. The console renders its notice into a browser and never into
Discord — it shows the action's own message verbatim (``templates/_notice.html``, whose docstring says
the message is the action's own) — so a sentence the ACTION built with ``display_name`` shows the
escapes to the person reading the banner.

The doubles here carry the REAL domain property (``display_name = Player.display_name`` /
``Server.display_name``, applied to the double by ``escaping``), so an install's escaping is reproduced
rather than imitated: a sentence that reads the wrong one of the two attributes fails below.

Sections: 1. the console notice through the app (the message pair), 2. the shared label readers,
3. the other player writes, 4. the server twin, 5. the Discord path, which must stay escaped.
"""
from __future__ import annotations

import html as html_module
import re
import types
from pathlib import Path

import pytest

#: the repository root — a source pin must not depend on the shell's working directory
REPO = Path(__file__).resolve().parents[1]

from core.data.player import Player
from core.data.server import Server
from core.utils.discord import escape_string
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

# the console write suites' own harness, reused rather than copied
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    Cluster, build, clean_registry, client, config_dir, instance, recording_bot, sign_in, token_of,
)
from test_webui_write_message import UCID_A  # noqa: F401  (the double is used by name)
from test_webui_write_player_actions import (  # noqa: F401  (the fixture is used by name)
    FakePlayer, ban_service, direct_ctx, player_server,
)

#: the page that renders the player controls: the dashboard Players tab. The Players page
#: is the database directory now and its rows carry no control.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"
POPUP = actions_page.POPUP_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX

#: a player name carrying every character ``escape_string`` mangles, and a server name carrying one
PLAYER_NAME = "[TAG]_Test*Pilot"
SERVER_NAME = "SRS-1"


def escaping(obj, prop):
    """*obj* carrying the ``display_name`` the DOMAIN computes for it — the real property, not a copy.

    ``Player.display_name``/``Server.display_name`` only read ``self.name``, so the property function
    can be applied to a double: what it returns is exactly what an install returns.
    """
    obj.display_name = prop.fget(obj)
    return obj


def notice_of(html: str) -> str:
    """The one-shot notice's own sentence, as the page rendered it (Jinja's escaping undone)."""
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    assert match, f"the page rendered no notice:\n{html}"
    return html_module.unescape(match.group(1))


def faithful_message_fleet():
    """The message suite's fleet with a player AND a server whose ``display_name`` is escaped."""
    from test_webui_write_message import Cluster, FakePlayer as MessagePlayer, instance, \
        server_with_players

    player = escaping(MessagePlayer(PLAYER_NAME, UCID_A), Player.display_name)
    srv = escaping(server_with_players(SERVER_NAME, ["Host A"], players=[player]),
                   Server.display_name)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": srv})
    return Cluster([srv], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", srv)])


def message_server_double():
    """The server twin's double, carrying the escaped ``display_name`` of an install."""
    from test_webui_server_message import MessageServer

    return escaping(MessageServer(SERVER_NAME, ["Host A"]), Server.display_name)


async def open_dialog(http, path: str, token: str) -> str:
    """Press one control's glyph: the row posts to its DIALOG, which renders the message field."""
    reply = await http.post(path + CONFIRM,
                            data={"server": SERVER_NAME, "ucid": UCID_A, "_csrf_token": token})
    assert reply.status_code == 200, reply.text
    return reply.text


def confirm_of(dialog: str) -> str:
    """The one-shot token the rendered dialog's form carries."""
    match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog)
    assert match, "the dialog rendered no confirm token"
    return match.group(1)


# ---------------------------------------------------- 1. the console notice, through the real app

@pytest.mark.asyncio
async def test_the_message_notice_names_the_player_and_the_server_plainly(config_dir,
                                                                         clean_registry,
                                                                         recording_bot):
    """ACCEPTANCE — the sentence the banner shows carries no escape for either name.

    The player double's ``display_name`` IS ``Player.display_name`` and the server's IS
    ``Server.display_name``, i.e. the names an install escapes for Discord.
    """
    cluster = faithful_message_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of((await http.get(ROSTER)).text)
        dialog = await open_dialog(http, POPUP, token)
        reply = await http.post(POPUP, data={"server": SERVER_NAME, "ucid": UCID_A,
                                             "message": "Check your six.",
                                             "_csrf_token": token,
                                             "_confirm_token": confirm_of(dialog)})
        assert reply.status_code == 303, reply.text
        after = (await http.get(ROSTER)).text

    sentence = notice_of(after)
    assert "\\" not in sentence, f"the notice carries an escaped name: {sentence!r}"
    assert sentence == f"Popup sent to {PLAYER_NAME} on server '{SERVER_NAME}'.", sentence


@pytest.mark.asyncio
async def test_the_chat_notice_names_the_player_and_the_server_plainly(config_dir, clean_registry,
                                                                     recording_bot):
    """The chat control's notice is the same action's other sentence — pinned separately."""
    cluster = faithful_message_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of((await http.get(ROSTER)).text)
        dialog = await open_dialog(http, actions_page.CHAT_PATH, token)
        reply = await http.post(actions_page.CHAT_PATH,
                               data={"server": SERVER_NAME, "ucid": UCID_A,
                                     "message": "Look alive.", "_csrf_token": token,
                                     "_confirm_token": confirm_of(dialog)})
        assert reply.status_code == 303, reply.text
        after = (await http.get(ROSTER)).text

    sentence = notice_of(after)
    assert "\\" not in sentence, f"the notice carries an escaped name: {sentence!r}"
    assert sentence == f"Chat message sent to {PLAYER_NAME} on server '{SERVER_NAME}'.", sentence


@pytest.mark.asyncio
async def test_the_audit_of_a_console_write_names_the_player_plainly():
    """The trail the console leaves is the same sentence — so it is checked on the action's result.

    ``audit_action`` composes its event from the result's own message (``core/actions.py``), so the
    audit line for a console write carries whatever the action named the player. The message is
    asserted directly here; section 1 shows the person reading the banner sees the same sentence.
    """
    from plugins.mission.actions import message_player

    player = escaping(FakePlayer(PLAYER_NAME, UCID_A), Player.display_name)
    srv = escaping(player_server(SERVER_NAME, ["Host A"], players=[player]), Server.display_name)
    result = await message_player(direct_ctx({srv.name: srv}), SERVER_NAME, UCID_A,
                                  "Check your six.")

    assert result.message == f"Popup sent to {PLAYER_NAME} on server '{SERVER_NAME}'."
    assert result.player_name == PLAYER_NAME, "the typed field carries the escaped name"


# ------------------------------------------------------------- 2. the shared label readers

def test_the_player_label_prefers_the_plain_name():
    """``_player_label``: ``name`` first, a display name only for a double that has nothing else."""
    from plugins.mission.actions import _player_label

    both = types.SimpleNamespace(name=PLAYER_NAME, display_name=escape_string(PLAYER_NAME))
    assert _player_label(both, UCID_A) == PLAYER_NAME
    fallback = types.SimpleNamespace(display_name="Test Pilot")
    assert _player_label(fallback, UCID_A) == "Test Pilot"
    assert _player_label(types.SimpleNamespace(name=""), UCID_A) == UCID_A


def test_the_server_label_prefers_the_plain_name():
    """``_server_label``: the same read one level up, with the caller's name as the last resort."""
    from plugins.mission.actions import _server_label

    both = types.SimpleNamespace(name=SERVER_NAME, display_name=escape_string(SERVER_NAME))
    assert _server_label(both, "fallback") == SERVER_NAME
    fallback = types.SimpleNamespace(display_name="SRS-2")
    assert _server_label(fallback, "fallback") == "SRS-2"
    assert _server_label(types.SimpleNamespace(name=""), "fallback") == "fallback"


# ------------------------------------------------------------- 3. the other player writes

@pytest.mark.asyncio
async def test_the_kick_notice_names_the_player_plainly():
    """``kick_player``'s sentence — one of the four that share ``_player_label``."""
    from plugins.mission.actions import kick_player

    player = escaping(FakePlayer(PLAYER_NAME, UCID_A), Player.display_name)
    srv = escaping(player_server(SERVER_NAME, ["Host A"], players=[player]), Server.display_name)
    result = await kick_player(direct_ctx({srv.name: srv}), SERVER_NAME, UCID_A, "teamkilling")

    assert result.message == (f"Player {PLAYER_NAME} (ucid={UCID_A}) kicked from server "
                              f"'{SERVER_NAME}'."), result.message
    assert "\\" not in result.message
    assert result.player_name == PLAYER_NAME


@pytest.mark.asyncio
async def test_the_ban_notice_names_the_player_plainly(ban_service):
    """``ban_player``'s two sentences (a period and a permanent ban)."""
    from plugins.mission.actions import ban_player

    player = escaping(FakePlayer(PLAYER_NAME, UCID_A), Player.display_name)
    srv = escaping(player_server(SERVER_NAME, ["Host A"], players=[player]), Server.display_name)
    ctx = direct_ctx({srv.name: srv}, ban_service)

    timed = await ban_player(ctx, SERVER_NAME, UCID_A, "cheating", "7")
    assert timed.message == f"Player {PLAYER_NAME} (ucid={UCID_A}) banned for 7 days on all servers."

    permanent = await ban_player(ctx, SERVER_NAME, UCID_A, "cheating")
    assert permanent.message == f"Player {PLAYER_NAME} (ucid={UCID_A}) banned permanently on all servers."
    assert "\\" not in timed.message and "\\" not in permanent.message


@pytest.mark.asyncio
async def test_the_mute_and_unmute_notices_name_the_player_plainly():
    """The mute pair shares ``_set_muted``, so one sentence shape covers both."""
    from plugins.mission.actions import mute_player, unmute_player

    player = escaping(FakePlayer(PLAYER_NAME, UCID_A), Player.display_name)
    srv = escaping(player_server(SERVER_NAME, ["Host A"], players=[player]), Server.display_name)
    ctx = direct_ctx({srv.name: srv})

    muted = await mute_player(ctx, SERVER_NAME, UCID_A)
    assert muted.message == f"Player {PLAYER_NAME} on server '{SERVER_NAME}' muted."

    unmuted = await unmute_player(ctx, SERVER_NAME, UCID_A)
    assert unmuted.message == f"Player {PLAYER_NAME} on server '{SERVER_NAME}' unmuted."
    assert "\\" not in muted.message and "\\" not in unmuted.message


@pytest.mark.asyncio
async def test_a_player_write_that_could_not_find_the_player_names_the_server_plainly():
    """The refusal's own sentence names the server too — it is the same label in the same action."""
    from plugins.mission.actions import kick_player

    srv = escaping(player_server(SERVER_NAME, ["Host A"]), Server.display_name)
    result = await kick_player(direct_ctx({srv.name: srv}), SERVER_NAME, UCID_A, "teamkilling")

    assert result.message == f"Player not found on server '{SERVER_NAME}'.", result.message


# -------------------------------------------------------------------- 4. the server twin

@pytest.mark.asyncio
async def test_the_server_message_notice_names_the_server_plainly():
    """``message_server``'s sentence — the server is its subject, read the same way."""
    from plugins.mission.actions import message_server

    srv = message_server_double()
    result = await message_server(direct_ctx({srv.name: srv}), SERVER_NAME, "Server down in 5.",
                                  "chat", "all")

    assert result.message == f"Chat message sent to everyone on server '{SERVER_NAME}'."
    assert "\\" not in result.message


# ------------------------------------------- 5. the Discord path stays escaped (unchanged)

def test_the_domain_still_escapes_the_display_name_for_discord():
    """The escaping exists for Discord's markdown and is untouched: it is what the commands send.

    The property functions are called directly, so this reads the domain's own behaviour rather than
    a copied expectation.
    """
    assert Player.display_name.fget(types.SimpleNamespace(name=PLAYER_NAME)) == \
        escape_string(PLAYER_NAME)
    assert Server.display_name.fget(types.SimpleNamespace(name=SERVER_NAME)) == \
        escape_string(SERVER_NAME)
    assert "\\" in escape_string(PLAYER_NAME) and "\\" in escape_string(SERVER_NAME)


def test_the_discord_commands_still_send_the_escaped_name():
    """The Discord commands never read this action, and their own sentences keep ``display_name``.

    Their wording is their own (``plugins/mission/commands.py``): a source pin, and the structural
    fact that the module does not reach the action layer at all.
    """
    source = (REPO / "plugins/mission/commands.py").read_text(encoding="utf-8")

    assert "call_action" not in source, "a Discord command started consuming the action layer"
    assert "from core.actions" not in source
    for qualname in ("kick_player", "ban_player", "mute_player", "unmute_player", "message_player",
                     "message_server"):
        assert qualname not in source, f"a Discord command reads the console's {qualname}"
    assert 'format(name=player.display_name, ucid=player.ucid)' in source, \
        "the Discord kick no longer names the player with the escaped display name"
