"""The player row's message pair: ONE glyph per mode, and the text is typed in the dialog.

The row used to carry the message control's inline text field plus its two mode buttons, so a player
row held three text inputs (a kick reason, a ban reason, the message) and every one of them ate the
width the server name needs. This pins the shape that replaces it:

* the strip renders GLYPHS ONLY — no text input and no mode buttons on any player row;
* ``chat`` and ``popup`` are two controls, each a glyph with a ``title`` and an ``aria-label`` naming
  the player and the server;
* each opens its OWN dialog, which carries the message field and posts with the mode the DECLARATION
  names (never a field the request sent), through the same ``message_player`` write as before;
* a POST that skips the dialog is refused, exactly like kick and ban;
* a caller without the capability is offered neither glyph.

Driven through the real app and the stub cluster; the domain boundary is the player double.
"""
from __future__ import annotations

import re

import pytest

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"

# the console write suites' own harness, reused rather than copied
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    build, clean_registry, client, config_dir, recording_bot, sign_in, token_of,
)
from test_webui_write_message import (  # noqa: F401  (the doubles are used by name)
    UCID_A, UCID_B, message_fleet,
)

CHAT = actions_page.CHAT_PATH
POPUP = actions_page.POPUP_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX
SERVER = "SRS-1"
SENDER = "mod1"


def acts_cells(html: str) -> list[str]:
    """Every player row's actions cell — the ONE column the strip renders into."""
    return re.findall(r'<td class="acts">.*?</td>', html, re.S)


def forms_for(html: str, path: str) -> list[str]:
    """Every rendered form whose ``action`` is *path*."""
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


def confirm_token_of(dialog: str) -> str:
    """The one-shot token the rendered dialog's form carries."""
    match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog)
    assert match, "the dialog rendered no confirm token"
    return match.group(1)


async def open_players(http) -> str:
    response = await http.get(ROSTER)
    assert response.status_code == 200, response.text
    return response.text


async def open_dialog(http, path: str, token: str, ucid: str = UCID_A) -> str:
    """Press one control's glyph: the row posts to its DIALOG, which renders the message field."""
    reply = await http.post(path + CONFIRM,
                            data={"server": SERVER, "ucid": ucid, "_csrf_token": token})
    assert reply.status_code == 200, reply.text
    return reply.text


# ------------------------------------------------- 1. the strip is glyphs, not a form of fields

@pytest.mark.asyncio
async def test_the_player_strip_renders_no_text_input_and_no_mode_buttons(config_dir,
                                                                         clean_registry):
    """The width the server name needs comes back: a player row holds GLYPHS.

    A ``<td class="acts">`` cell carrying an ``<input type="text">`` is the inline field this card
    removes — for the message control AND for the kick/ban reasons, whose field belongs to their
    dialogs. ``name="mode"`` is the old pair of submit buttons, one per mode.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
    cells = acts_cells(page)
    assert cells, "the players page rendered no actions cell"
    for cell in cells:
        assert '<input type="text"' not in cell, f"a row still renders a text field: {cell}"
        assert 'name="mode"' not in cell, "a row still renders the mode buttons"
    assert "act-strip" in page, "the strip itself must still be there"


@pytest.mark.asyncio
async def test_the_two_message_glyphs_are_present_for_a_player_row(config_dir, clean_registry):
    """``chat`` and ``popup`` are two controls of the one ``players.message`` write, each a glyph
    with a native ``title`` AND an ``aria-label`` naming the player and the server."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
        tab = (await http.get("/?tab=players")).text
    for html in (page, tab):
        assert forms_for(html, CHAT + CONFIRM), "the chat control posts to its dialog"
        assert forms_for(html, POPUP + CONFIRM), "the popup control posts to its dialog"
        assert 'title="Chat — a chat line only this player reads"' in html
        assert 'aria-label="Send a chat line to Fragmagnet on SRS-1"' in html
        assert 'title="Popup — a private message in game"' in html
        assert 'aria-label="Send a popup to Fragmagnet on SRS-1"' in html


# ------------------------------------------------------------- 2. the dialog carries the message

@pytest.mark.asyncio
async def test_each_dialog_posts_the_message_with_the_mode_its_declaration_names(
        config_dir, clean_registry, recording_bot):
    """The mode is the CONTROL's own, never a field the request sent.

    The popup write is deliberately posted with ``mode=chat`` in its body: the console passes the
    declaration's value, so the popup still reaches ``sendPopupMessage``.
    """
    cluster = message_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))

        chat_dialog = await open_dialog(http, CHAT, token)
        assert 'name="message"' in chat_dialog, "the dialog asks for the message"
        assert f'maxlength="{actions_page.MESSAGE_FIELD_MAX}"' in chat_dialog
        chat = await http.post(CHAT, data={"server": SERVER, "ucid": UCID_A,
                                           "message": "Look alive.", "_csrf_token": token,
                                           "_confirm_token": confirm_token_of(chat_dialog)})

        popup_dialog = await open_dialog(http, POPUP, token)
        popup = await http.post(POPUP, data={"server": SERVER, "ucid": UCID_A,
                                             "message": "Check your six.", "mode": "chat",
                                             "_csrf_token": token,
                                             "_confirm_token": confirm_token_of(popup_dialog)})

    assert chat.status_code == 303 and popup.status_code == 303
    assert player.calls == [("chat", "Look alive.", SENDER),
                            ("popup", "Check your six.", -1, SENDER)], \
        "each control must reach the same write with its own mode"
    assert [call[0] for call in recording_bot.calls] == [
        "Chat message sent to Fragmagnet on server 'SRS-1'.",
        "Popup sent to Fragmagnet on server 'SRS-1'.",
    ]


@pytest.mark.asyncio
async def test_a_post_that_skips_the_dialog_is_refused(config_dir, clean_registry,
                                                       recording_bot):
    """``POST`` is not ``write`` for these two either: the row posts to the dialog, the dialog's form
    carries the one-shot token, and a caller who skips it is refused before the action runs."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    player = cluster.servers[0].players[UCID_A]
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        skipped = await http.post(CHAT, data={"server": SERVER, "ucid": UCID_A,
                                              "message": "hi", "_csrf_token": token},
                                  headers={"Accept": "text/html"})
    assert skipped.status_code == 403, skipped.text
    assert player.calls == [], "a POST that skipped the dialog reached the write"
    assert recording_bot.calls == []


# ------------------------------------------------------------------- 3. the omitted glyphs

@pytest.mark.asyncio
async def test_an_identity_without_the_capability_is_offered_neither_glyph(config_dir,
                                                                          clean_registry):
    """No capability, no glyph — and no line in the markup that names either route."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "plain")                 # ``DCS`` only: not a console reader
        page = await http.get(ROSTER)
        assert page.status_code == 403, page.text
        assert "act-btn" not in page.text and "act-strip" not in page.text
        assert CHAT not in page.text and POPUP not in page.text
        reply = await http.post(CHAT, data={"server": SERVER, "ucid": UCID_A, "message": "hi",
                                            "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"


@pytest.mark.asyncio
async def test_a_player_row_keeps_the_glyph_only_strip_and_no_overflow_trigger(config_dir,
                                                                              clean_registry):
    """The row is ONE width: five glyphs in the strip, no ``⋮`` menu (a player row never had one),
    and no field — which is where the room for the server name comes back from."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
    assert page.count('class="act-strip') == len(acts_cells(page)), "one strip per player row"
    assert 'class="act-btn more"' not in page and "act-menu-p" not in page
    assert "act-strip wide" not in page, "the wide class belongs to a row with an overflow menu"
    cell = acts_cells(page)[0]
    assert cell.count('class="act-btn') == 5, "kick · ban · chat · popup · mute"
    assert "<label" not in cell, "a row field would carry its label — none is rendered"
    assert "16rem" not in cell


@pytest.mark.asyncio
async def test_a_plugin_the_install_does_not_have_offers_neither_glyph(config_dir, clean_registry):
    """The action layer absent (no ``mission`` plugin): both controls are omitted, not rendered dead."""
    app = build(config_dir, message_fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_players(http)
    assert not forms_for(page, CHAT + CONFIRM) and not forms_for(page, POPUP + CONFIRM)
    assert 'aria-label="Send a chat line to Fragmagnet on SRS-1"' not in page


# ------------------------------------------------------- 4. the field is a box, not a one-liner

@pytest.mark.asyncio
async def test_the_message_field_is_a_multi_line_textarea(config_dir, clean_registry):
    """The message is prose: both dialogs collect it in a TEXTAREA, at the declaration's own cap.

    The one-line ``<input>`` was the field's own defect — a message of a few lines is invisible past
    the input's right edge — while the length cap and the ``required`` flag stay the declaration's.
    """
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        for path in (CHAT, POPUP):
            dialog = await open_dialog(http, path, token)
            match = re.search(r"<textarea[^>]*>", dialog)
            assert match, f"{path} renders no textarea:\n{dialog}"
            tag = match.group(0)
            assert 'name="message"' in tag, tag
            assert 'rows="5"' in tag, tag
            assert f'maxlength="{actions_page.MESSAGE_FIELD_MAX}"' in tag, tag
            assert "required" in tag, tag
            assert '<input type="text" id="message-dialog"' not in dialog, dialog


@pytest.mark.asyncio
async def test_the_player_dialog_keeps_cancel_and_send_on_the_bottom_line(config_dir,
                                                                         clean_registry):
    """A player dialog with a message box puts both controls on its LAST line, below the field."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_players(http))
        dialog = await open_dialog(http, POPUP, token)

    footer = dialog[dialog.index('class="ft"'):]
    assert footer.index("<textarea") < footer.index('class="dlg-buttons"'), \
        "the button row must come after the field"
    row = footer[footer.index('class="dlg-buttons"'):]
    assert row.index(">Cancel</a>") < row.index('type="submit"'), \
        "Cancel and the primary must share the row, Cancel first"
