"""The two player-message dialogs: the copy must match the call, and a label is a PLAIN name.

The chat line is ``Player.sendChatMessage(body, sender)`` — ``{"command": "sendChatMessage", "to":
self.id, ...}`` (``core/data/player.py``), i.e. a line for THAT PLAYER (in the game it is
``net.send_chat_to``), so the dialog that claimed "every player on {server} sees the same line" was
false, and the popup's own copy measured itself against that falsehood ("unlike a chat line"). Both
dialogs are private; they differ in FORM (a line in the chat versus a box of its own), and the copy
now says exactly that.

The NAME a dialog shows is read as the PLAIN name. The domain objects carry it twice: ``name`` plain
and ``display_name`` escaped for Discord's markdown (``core/data/player.py``:
``utils.escape_string``), so the console's ``display_name``-first read rendered
``\\[TAG\\] Test Pilot`` for a name that has brackets. Driven through the real app and the stub
cluster; the domain boundary is the player double.
"""
from __future__ import annotations

import glob
import re
import types

import pytest

from core.data.player import Player
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"

# the console write suites' own harness, reused rather than copied
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    PASSWORD, build, clean_registry, client, config_dir, recording_bot, sign_in, token_of,
)
from test_webui_write_message import (  # noqa: F401  (the doubles are used by name)
    UCID_A, message_fleet,
)

CHAT = actions_page.CHAT_PATH
POPUP = actions_page.POPUP_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX
SERVER = "SRS-1"
PLAYER = "Fragmagnet"
BRACKETED = "[TAG] Test Pilot"

#: the claim the chat dialog used to make, in every spelling it carried it
FALSE_CLAIMS = (
    "everyone on it reads this line",
    "visible to everyone",
    "and so does everyone else",
    "sees the same line",
    "It is not a direct message",
)


class DomainNamedPlayer:
    """A player double whose ``display_name`` is the REAL domain property (no shadowing).

    The property is ``utils.escape_string(self.name)`` — the escaping the console must not render —
    so this double reproduces an install's name exactly instead of imitating it.
    """

    display_name = Player.display_name

    def __init__(self, name, ucid, *, active=True):
        self.name = name
        self.ucid = ucid
        self.active = active
        self.unit_display_name = "A-10C II"
        self.group_name = "Hog 1"
        self.id = 101
        self.unit_name = "unit-101"
        self.server = None
        self.calls: list[tuple] = []

    async def sendChatMessage(self, message, sender=None):
        self.calls.append(("chat", message, sender))

    async def sendPopupMessage(self, message, timeout=-1, sender=None):
        self.calls.append(("popup", message, timeout, sender))


def fleet_with(player):
    """The message fleet from the write suite, with ONE player of this test's own making."""
    from test_webui_write_message import Cluster, instance, server_with_players

    srv = server_with_players(SERVER, ["Host A"], players=[player])
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": srv})
    return Cluster([srv], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", srv)])


async def open_dialog(http, path: str, ucid: str = UCID_A) -> str:
    """Press one message glyph: the row posts to its DIALOG, which renders the copy and the field."""
    response = await http.get(ROSTER)
    assert response.status_code == 200, response.text
    reply = await http.post(path + CONFIRM,
                            data={"server": SERVER, "ucid": ucid,
                                  "_csrf_token": token_of(response.text)})
    assert reply.status_code == 200, reply.text
    return reply.text


def dialog_text(html: str) -> str:
    """The dialog's own copy — its warning block, its detail and the field's help, as rendered."""
    parts = re.findall(r'(?:<div class="dlg-warn">.*?</div>|<p class="dlg-what">.*?</p>'
                       r'|<span class="hint"[^>]*>.*?</span>)', html, re.S)
    assert parts, f"the dialog rendered no copy at all:\n{html}"
    return " ".join(parts)


# ------------------------------------------------------------------ 1. what the chat dialog says

@pytest.mark.asyncio
async def test_the_chat_dialog_never_says_the_whole_server_reads_the_line(config_dir,
                                                                         clean_registry):
    """ACCEPTANCE — the line is addressed to ONE player; the copy says so.

    The write is ``{"command": "sendChatMessage", "to": self.id, ...}`` (the game's
    ``net.send_chat_to``), so every sentence that claimed a server-wide audience was untrue.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        text = dialog_text(await open_dialog(http, CHAT))

    for claim in FALSE_CLAIMS:
        assert claim not in text, f"the chat dialog still claims '{claim}':\n{text}"
    assert f"Only {PLAYER} reads this line." in text, text
    assert f"nobody else on {SERVER} sees it" in text, text


@pytest.mark.asyncio
async def test_the_chat_dialog_help_and_hint_stop_promising_a_server_wide_audience(config_dir,
                                                                                   clean_registry):
    """The field's help AND the control's hint are copy too — both carried the same false claim."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = (await http.get(ROSTER)).text
        dialog = await open_dialog(http, CHAT)

    assert "Sent in the server's chat" not in dialog, dialog
    assert "only they read it" in dialog, dialog
    assert 'title="Chat — a chat line in game, visible to everyone"' not in page, page
    assert "visible to everyone" not in page, page


# ------------------------------------------------------------------ 2. what the popup dialog says

@pytest.mark.asyncio
async def test_the_popup_dialog_does_not_measure_itself_against_a_chat_line(config_dir,
                                                                            clean_registry):
    """The popup's own copy compared itself to a chat line that was never what it claimed.

    With the chat line corrected, "unlike a chat line" is false in the other direction: both are
    addressed to one player only. The popup stays true and distinct by FORM — a box of its own.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        text = dialog_text(await open_dialog(http, POPUP))

    assert "unlike a chat line" not in text, text
    assert f"addressed to {PLAYER} alone" in text, text
    assert "in a box of its own, not as a line in their chat" in text, text


@pytest.mark.asyncio
async def test_both_dialogs_state_one_audience_and_differ_by_form(config_dir, clean_registry):
    """Both are private; only the FORM tells them apart — a line in the chat versus a box.

    A dialog that has to say "unlike the other one" to be understood is the shape this pins away.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        chat = dialog_text(await open_dialog(http, CHAT))
        popup = dialog_text(await open_dialog(http, POPUP))

    assert "chat" in chat and "box" in popup, (chat, popup)
    assert "box" not in chat, f"the chat dialog describes the popup's form:\n{chat}"
    assert "everyone" not in popup and "everyone" not in chat, (chat, popup)


# ------------------------------------------------------------------------------- 3. the catalogs

def test_no_catalog_carries_the_claim_that_the_whole_server_reads_a_chat_line():
    """ACCEPTANCE — the untrue sentence is gone from every catalog, obsolete entries included.

    ``pybabel update`` keeps a replaced msgid as a ``#~`` obsolete entry; gettext ignores those, so
    a catalog can look clean while the sentence is still sitting in the file for the next
    extraction to revive. The scan covers the whole file, not only the live entries.
    """
    catalogs = sorted(glob.glob("locale/*/LC_MESSAGES/webui.po"))
    assert catalogs, "no catalogs found - the scan would pass for the wrong reason"
    for path in catalogs:
        body = open(path, encoding="utf-8").read()
        for claim in FALSE_CLAIMS:
            assert claim not in body, f"{path} still carries '{claim}'"
        assert "unlike a chat line" not in body, f"{path} still carries 'unlike a chat line'"


# --------------------------------------------------------------------------- 4. the label is plain

@pytest.mark.asyncio
async def test_the_dialog_label_is_the_plain_name_when_the_name_has_brackets(config_dir,
                                                                            clean_registry):
    """ACCEPTANCE — a name containing ``[`` and ``]`` renders as it is, with no backslash escapes.

    The double's ``display_name`` IS ``core.data.player.Player.display_name``, i.e. the name escaped
    by ``utils.escape_string``; the dialog must read ``name``.
    """
    player = DomainNamedPlayer(BRACKETED, UCID_A)
    app = build(config_dir, fleet_with(player))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http, CHAT)
        text = dialog_text(dialog)

    assert "\\[" not in dialog and "\\]" not in dialog, \
        f"the dialog still renders the markdown-escaped name:\n{text}"
    assert BRACKETED in text, text


@pytest.mark.asyncio
async def test_the_label_keeps_underscores_and_stars_unescaped(config_dir, clean_registry):
    """The escaping mangles ``_`` and ``*`` too (``escape_string``): the plain name keeps them."""
    name = "[TAG]_P*X"
    player = DomainNamedPlayer(name, UCID_A)
    app = build(config_dir, fleet_with(player))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http, POPUP)

    assert name in dialog, dialog
    assert "\\_" not in dialog and "\\*" not in dialog, \
        f"the popup dialog renders an escaped name:\n{dialog_text(dialog)}"


# ---------------------------------------------------------------------------- 5. the sender label

#: a local account whose NAME carries every character the domain escaper mangles — the sender label
#: travels to the game, so it must arrive as written.
SENDER_NAME = "[TAG]_P*X"


def _sender_users():
    from test_webui_write_pause import _hash

    return [{"username": "mod1", "password_hash": _hash(), "roles": ["DCS Admin"],
             "scope": ["Host A"]},
            {"username": SENDER_NAME, "password_hash": _hash(), "roles": ["DCS Admin"],
             "scope": ["Host A"]}]


@pytest.mark.asyncio
async def test_the_sender_label_the_action_receives_carries_no_escapes(config_dir, clean_registry,
                                                                       recording_bot):
    """The label the domain call is handed is the plain name — no backslashes, nothing mangled.

    The label travels into the game chat (``from``), so an escaped name would be visible to the
    player. The identity path does not escape today (``_sender`` never went through
    ``utils.escape_string``); this pins that, through the route, so a future escaping of the label
    cannot arrive unnoticed.
    """
    cluster = message_fleet()
    app = build(config_dir, cluster, users=_sender_users())
    player = cluster.servers[0].players[UCID_A]

    async with client(app) as http:
        await sign_in(http, SENDER_NAME)
        dialog = await open_dialog(http, CHAT)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', dialog).group(1)
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog).group(1)
        reply = await http.post(CHAT, data={"server": SERVER, "ucid": UCID_A, "message": "hi",
                                            "_csrf_token": token, "_confirm_token": confirm})

    assert reply.status_code == 303, reply.text
    assert player.calls == [("chat", "hi", SENDER_NAME)], player.calls
    sender = player.calls[0][2]
    assert "\\" not in sender, f"the sender label carries an escape: {sender!r}"


def test_the_plain_name_reader_prefers_the_plain_attribute():
    """``_plain_name``: ``name`` first, a display name only when there is no plain one."""
    escaped = types.SimpleNamespace(name="[TAG] Test Pilot",
                                    display_name="\\[TAG\\] Test Pilot")
    assert actions_page._plain_name(escaped, ("name", "display_name")) == "[TAG] Test Pilot"
    fallback = types.SimpleNamespace(display_name="Test Pilot")
    assert actions_page._plain_name(fallback, ("name", "display_name")) == "Test Pilot"
    assert actions_page._plain_name(types.SimpleNamespace(), ("name",)) == ""
