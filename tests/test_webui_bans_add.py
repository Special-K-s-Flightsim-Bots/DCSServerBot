"""The Bans page's ADD flow: add a ban BY UCID, end to end — offline and unknown players included.

One section per acceptance criterion of the card:

* the ADD bans a ucid that is NOT an online player, and one the installation has never seen: nothing
  on that path resolves a server or looks a player up;
* a scoped add for a caller holding N servers writes N rows sharing ONE ``banned_at``, and the list
  renders them as ONE entry;
* an empty target set (a global caller) writes ONE bot-wide row (``server_name IS NULL``);
* the duration goes through ``parse_ban_days``: empty is the permanent sentinel, ``N`` is N days, and
  0 / negative / non-numeric / absurd is a typed refusal with NO row written;
* the action refuses a non-web transport;
* the PAGE-LEVEL control is the ``bans`` table's own (``PAGE_CONTROLS``) and is not a row control;
* the dialog asks for the ucid, the reason, the duration and the scope — with the reason and the
  duration built by the SAME builder the player-row Ban dialog uses — and mints NO one-shot token;
* the SCOPE picker follows the caller's KIND: an UNRESTRICTED caller (no target set) may name ONE
  server and gets a truthful bot-wide word, a narrowed holder keeps its own servers and the
  ``All my servers`` word, and a caller whose scope matches nothing keeps the bot-wide option alone;
* the new msgids are in the ``webui`` catalogs (and their recompiled ``.mo``), and the action's
  sentences are in the ``mission`` catalogs.

Driven through the REAL application (the production factory, the shell, the local login backend and
a stub source) with the ban list behind a stub ServiceBus, exactly like the slice-1 suite this file
extends; the harness is imported from it rather than copied.
"""
from __future__ import annotations

import datetime
import gettext
import re
import types
from pathlib import Path

import pytest

from core.actions import ActionContext
from plugins.mission.actions import BANS_CONSOLE_ONLY, ban_ucid
from test_webui_bans_page import (  # noqa: F401 - the ban harness, reused on purpose
    NEVER, NOW, FakeBus, _row, _transport_context, ban_bus, bans_page_html,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures reused on purpose
    Cluster, _hash, _users, build, clean_registry, client, config_dir, fleet, instance,
    recording_bot, server, sign_in, token_of,
)
from services.webservice.pages import actions as actions_page
from services.webservice.pages import bans as bans_page
from services.webservice.scope import Scope

REPO = Path(__file__).resolve().parent.parent
ADD = actions_page.BAN_ADD_PATH
ADD_DIALOG = ADD + actions_page.CONFIRM_SUFFIX
#: A REAL UCID: 32 characters, alphanumeric, lowercase (``core.utils.helper.is_ucid``) — the check
#: the Discord ``/dcs ban`` uses, so a shorter or upper-case one is refused before anything is read.
UCID = "4b1f9c22" * 4
UNKNOWN_UCID = "deadbeef" * 4
LIFT_TITLE = "Lift — remove this ban from the list"


@pytest.fixture
def two_servers():
    """A cluster whose ``Host A`` holder manages TWO servers — the N-row scope case."""
    a = server("SRS-1", ["Host A"])
    a2 = server("SRS-2", ["Host A"], instance="Host-A2-Instance")
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a),
                                              "Host-A2-Instance": instance("Host-A2-Instance", a2)})
    return Cluster([a, a2, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-A2-Instance", a2),
                              instance("Host-B-Instance", b)])


async def add(http, *, ucid=UCID, reason="cheating", days="", scope="", dialog=True):
    """Run the WHOLE ADD: the page's control, the dialog, then the dialog's own write."""
    page = await bans_page_html(http)
    if dialog:
        reply = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
        assert reply.status_code == 200, reply.text
    return await http.post(ADD, data={"ucid": ucid, "reason": reason, "days": days, "scope": scope,
                                      "_csrf_token": token_of(page)})


def written(bus) -> list[dict]:
    """The rows the ADD asked the bus to write, in order."""
    return list(bus.bans_written)


def stamps(bus) -> set:
    """The ``banned_at`` stamps of those writes — ONE value when a scoped ban is one entry."""
    return {row["banned_at"] for row in written(bus)}


# ------------------------------------------------------------- 1. the page-level control


@pytest.mark.asyncio
async def test_the_bans_page_head_offers_the_add_control(config_dir, clean_registry, ban_bus):
    """``Add ban…`` is the ``bans`` table's PAGE control: ONE POST form whose answer is the dialog."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert "Add ban…" in html, "the page head carries no Add ban control"
    assert html.count(f'action="{ADD_DIALOG}"') == 1, \
        "the page-level control is not rendered exactly once in the page head"
    assert f'action="{ADD}"' not in html, "the page offers the write path as well as the dialog's"


@pytest.mark.asyncio
async def test_the_add_control_is_the_bans_tables_own(config_dir, clean_registry, ban_bus):
    """Asked BY TABLE: the Servers page is handed its own control and never the bans one."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        servers = (await http.get("/fleet")).text
    assert f'action="{ADD_DIALOG}"' not in servers, "the bans control appeared on another page"
    assert actions_page.PAGE_CONTROLS["bans"] == (actions_page.BAN_ADD_ACTION,)
    assert actions_page.PAGE_CONTROLS["servers"] == actions_page.SERVER_PAGE_ACTIONS
    assert actions_page.BAN_ADD_ACTION not in actions_page.SERVER_PAGE_ACTIONS


def test_the_add_is_the_page_control_and_the_lift_is_the_row_control():
    """One declaration each, and the two are distinct: a row offers no ADD, a page no LIFT."""
    assert actions_page.BAN_ADD_ACTION.qualname == "ban_ucid"
    assert actions_page.BAN_LIFT_ACTION.qualname == "unban"
    assert actions_page.BAN_ADD_ACTION.confirm is False, "the ADD declares a confirmation it has none"
    assert actions_page.BAN_ADD_ACTION.form is True, "the ADD must reach its own form"
    assert actions_page.BAN_LIFT_ACTION.confirm is True
    assert actions_page.BAN_ADD_ACTION.path != actions_page.BAN_LIFT_ACTION.path


# ------------------------------------------------------------- 2. the write itself


@pytest.mark.asyncio
async def test_an_add_bans_a_ucid_that_is_not_an_online_player(config_dir, clean_registry, ban_bus):
    """No player is resolved and none has to exist: the ban is written for the ucid alone."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID, reason="cheating")
    assert reply.status_code == 303, reply.text
    assert [(row["ucid"], row["reason"]) for row in written(ban_bus)] == [(UCID, "cheating")]
    assert [row["server_name"] for row in written(ban_bus)] == [None], \
        "an unscoped Admin's add is the one BOT-WIDE row"


@pytest.mark.asyncio
async def test_an_add_bans_a_ucid_we_have_never_seen(config_dir, clean_registry, ban_bus):
    """An unknown ucid is not a refusal: the ban list is keyed on the ucid, not on a known player."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UNKNOWN_UCID, reason="aimbot")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert [row["ucid"] for row in written(ban_bus)] == [UNKNOWN_UCID]
    assert UNKNOWN_UCID in html, "the added ban is not in the list"
    assert "aimbot" in html


@pytest.mark.asyncio
async def test_the_ban_is_attributed_to_the_acting_person(config_dir, clean_registry, ban_bus):
    """``banned_by`` is the person's own shown name — never nobody, never a browser field."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID)
    assert reply.status_code == 303
    assert written(ban_bus)[0]["banned_by"] == "specialk", \
        "the ban list did not record who placed the ban"


@pytest.mark.asyncio
async def test_a_scoped_add_writes_n_rows_with_one_stamp_and_renders_one_entry(
        config_dir, clean_registry, ban_bus, two_servers):
    """The Host-A holder manages two servers: two rows, ONE ``banned_at``, ONE entry in the list."""
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await add(http, ucid=UCID, reason="teamkilling")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    rows = written(ban_bus)
    assert sorted(row["server_name"] for row in rows) == ["SRS-1", "SRS-2"], \
        "the add did not cover exactly the caller's own servers"
    assert len(stamps(ban_bus)) == 1, "the N rows of one ban must share ONE banned_at stamp"
    assert html.count(LIFT_TITLE) == 1, "the entry was rendered as more than one row"
    assert "SRS-1, SRS-2" in html, "the one entry does not name both servers it covers"


@pytest.mark.asyncio
async def test_an_empty_target_set_writes_one_bot_wide_row(config_dir, clean_registry, ban_bus):
    """A global caller (an Admin) has no target set: ONE row with no server (the retained rule)."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert [row["server_name"] for row in written(ban_bus)] == [None]
    assert "all servers" in html, "a bot-wide ban did not render as such"


# ------------------------------------------------------------- 3. the duration


@pytest.mark.asyncio
async def test_an_empty_duration_is_the_permanent_sentinel(config_dir, clean_registry, ban_bus):
    """An empty duration means permanent: the year-9999 sentinel, rendered as the word ``never``."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID, days="")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus)[0]["days"] is None, "an empty duration is not a day count"
    assert ban_bus.rows[0]["banned_until"].year == 9999, "the permanent sentinel was not written"
    assert '<span class="tag">never</span>' in html


@pytest.mark.asyncio
@pytest.mark.parametrize("days", ["1", "7", "365"])
async def test_a_day_count_writes_that_many_days(config_dir, clean_registry, ban_bus, days):
    """``N`` days is N days from now — not the sentinel, and not a rounded approximation."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID, days=days)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus)[0]["days"] == int(days)
    until = ban_bus.rows[0]["banned_until"]
    assert until.year != 9999, "a dated ban was written as permanent"
    assert until - datetime.datetime.now() > datetime.timedelta(days=int(days) - 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("days", ["0", "-3", "abc", "7.5", "99999999"])
async def test_a_refused_duration_writes_nothing(config_dir, clean_registry, ban_bus, days):
    """0, negative, non-numeric and absurd are TYPED refusals — and no row is written."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        reply = await http.post(ADD, data={"ucid": UCID, "reason": "cheating", "days": days,
                                           "scope": "", "_csrf_token": token_of(page)})
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], f"a refused duration ({days}) still reached the bus"
    assert ban_bus.rows == [], f"a refused duration ({days}) wrote a row"
    assert "Days must" in html, f"the refusal for '{days}' was not shown"


# ------------------------------------------------------------- 4. the other refusals


@pytest.mark.asyncio
async def test_an_invalid_ucid_is_refused_with_nothing_written(config_dir, clean_registry, ban_bus):
    """The check the Discord ``/dcs ban`` uses: a 32-character lowercase alphanumeric ucid."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid="not-a-ucid")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [] and ban_bus.rows == []
    assert "is not a valid UCID" in html


@pytest.mark.asyncio
async def test_an_empty_reason_is_refused_with_nothing_written(config_dir, clean_registry, ban_bus):
    """A ban with no reason leaves nobody able to say why — the console's field is required."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID, reason="   ")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [] and ban_bus.rows == []
    assert "A reason is required" in html


@pytest.mark.asyncio
async def test_a_scope_outside_the_callers_set_is_refused_with_nothing_written(
        config_dir, clean_registry, ban_bus, two_servers):
    """The select offers the caller's own servers; the ACTION re-checks a crafted one."""
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await add(http, ucid=UCID, scope="SRS-B")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], "a foreign server was banned from a scoped caller"
    assert ban_bus.rows == []
    assert "outside your scope" in html


@pytest.mark.asyncio
async def test_the_add_refuses_a_non_web_transport(ban_bus):
    """MCP / REST carry no scope: a hand-built or service context is refused before any write."""
    for ctx in (_transport_context(""), ActionContext.from_service(
            types.SimpleNamespace(node=types.SimpleNamespace()))):
        result = await ban_ucid(ctx, UCID, "cheating")
        assert result.success is False
        assert result.message == BANS_CONSOLE_ONLY
    assert written(ban_bus) == [], "a non-console caller reached the ban list"


# ------------------------------------------------------------- 4b. the scope picker (caller kinds)

#: A NARROWED caller whose scope names NO server: a declared ``managed_by`` value the fleet does not
#: carry. Its target set is empty, exactly like an Admin's — the case the picker must NOT conflate.
GHOST = {"username": "ghost", "roles": ["DCS"], "scope": ["Host Z"]}
EVERY = "Every server (bot-wide)"
ALL_MINE = "All my servers"


def _narrowed_with_nothing():
    """The users list for the ``ghost`` caller beside the shared ones."""
    return [*_users(), {**GHOST, "password_hash": _hash()}]


def test_the_nameable_servers_follow_the_callers_kind():
    """``_caller_ban_servers``: the whole view when UNSCOPED, the own target set when narrowed.

    The empty set a narrowed caller's scope leaves is NOT an Admin's: the two are told apart by the
    scope's own ``unrestricted`` flag, and only the unscoped caller may name an arbitrary server.
    """
    from plugins.mission.actions import _caller_ban_servers

    servers = {"SRS-1": server("SRS-1", ["Host A"]), "SRS-B": server("SRS-B", ["Host B"]),
               "SRS-U": server("SRS-U")}
    named = types.SimpleNamespace
    assert _caller_ban_servers(named(_scope=Scope.everything(), servers=servers)) == \
        ["SRS-1", "SRS-B", "SRS-U"], "an unscoped caller could not name every server in its view"
    assert _caller_ban_servers(named(_scope=Scope.restricted(["Host A"]), servers=servers)) == \
        ["SRS-1"], "a narrowed caller could name a foreign server"
    assert _caller_ban_servers(named(_scope=Scope.restricted(["Host Z"]), servers=servers)) == [], \
        "a scope matching nothing became able to name a server"


@pytest.mark.asyncio
async def test_an_unrestricted_caller_can_add_a_ban_for_one_named_server(
        config_dir, clean_registry, ban_bus):
    """An Admin has no target set; naming ONE server writes THAT server's single row — not the global."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await add(http, ucid=UCID, reason="one server only", scope="SRS-1")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    rows = written(ban_bus)
    assert [row["server_name"] for row in rows] == ["SRS-1"], \
        "an unrestricted caller could not name ONE server"
    assert [row["ucid"] for row in rows] == [UCID]
    assert "SRS-1" in html


@pytest.mark.asyncio
async def test_the_picker_offers_every_server_to_an_unrestricted_caller(
        config_dir, clean_registry, ban_bus):
    """An Admin is offered the bot-wide option with the unscoped WORD, plus one option per server."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert '<option value="SRS-1">SRS-1</option>' in text, "the picker offered no per-server option"
    assert '<option value="SRS-B">SRS-B</option>' in text
    assert f">{EVERY}</option>" in text, "the unscoped bot-wide word is missing"
    assert f">{ALL_MINE}</option>" not in text, "an unscoped caller has no 'my servers'"


@pytest.mark.asyncio
async def test_the_picker_keeps_the_callers_own_servers_and_word_for_a_narrowed_holder(
        config_dir, clean_registry, ban_bus, two_servers):
    """A narrowed holder keeps today's picker: its own servers, the scoped word, and no foreign one."""
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await bans_page_html(http)
        dialog = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert f">{ALL_MINE}</option>" in text, "the scoped bot-wide word was changed"
    assert '<option value="SRS-1">SRS-1</option>' in text
    assert '<option value="SRS-2">SRS-2</option>' in text
    assert "SRS-B" not in text, "the picker offered a server outside the caller's scope"
    assert EVERY not in text, "a narrowed holder was given the unscoped word"


@pytest.mark.asyncio
async def test_the_picker_offers_only_the_bot_wide_option_when_the_scope_matches_nothing(
        config_dir, clean_registry, ban_bus):
    """A narrowed caller whose scope names no server keeps the bot-wide option ALONE — truthful word."""
    app = build(config_dir, fleet(), users=_narrowed_with_nothing())
    async with client(app) as http:
        await sign_in(http, "ghost")
        page = await bans_page_html(http)
        dialog = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert f">{EVERY}</option>" in text, "the bot-wide word does not state the global write"
    assert f">{ALL_MINE}</option>" not in text, "'my servers' is untrue for a scope matching nothing"
    assert '<option value="SRS-1"' not in text and '<option value="SRS-B"' not in text, \
        "a caller whose scope matches nothing was offered a server to name"


@pytest.mark.asyncio
async def test_a_narrowed_caller_is_still_refused_a_foreign_server_with_the_picker_widened(
        config_dir, clean_registry, ban_bus):
    """The widened picker does NOT widen the write: a narrowed caller naming a foreign server is refused."""
    app = build(config_dir, fleet(), users=_narrowed_with_nothing())
    async with client(app) as http:
        await sign_in(http, "ghost")
        reply = await add(http, ucid=UCID, scope="SRS-1")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], "a narrowed caller named a server its scope does not carry"
    assert ban_bus.rows == []
    assert "outside your scope" in html


# ------------------------------------------------------------- 5. the dialog


@pytest.mark.asyncio
async def test_the_dialog_asks_for_the_ucid_the_reason_the_duration_and_the_scope(
        config_dir, clean_registry, ban_bus, two_servers):
    """The mockup's four fields, from the shared ban-field builder, and NO one-shot token."""
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await bans_page_html(http)
        dialog = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert f'action="{ADD}"' in text, "the dialog does not post to the ADD"
    assert "Add a ban?" in text, "the dialog lost the mockup's heading"
    assert "Ban player" in text, "the dialog lost its primary button's word"
    # the required target
    ucid_input = re.search(r'<input type="text" id="ucid-dialog"[^>]*>', text)
    assert ucid_input is not None, "the dialog asks for no ucid"
    assert "required" in ucid_input.group(0) and 'maxlength="64"' in ucid_input.group(0)
    assert "Player (UCID)" in text
    # the reason — the player-row Ban dialog's own record
    reason_input = re.search(r'<input type="text" id="reason-dialog"[^>]*>', text)
    assert reason_input is not None, "the dialog asks for no reason"
    assert "required" in reason_input.group(0) and 'maxlength="80"' in reason_input.group(0)
    # the duration — the SAME record, bounds and all, and NOT required (empty is permanent)
    days_input = re.search(r'<input type="number" id="days-dialog"[^>]*>', text)
    assert days_input is not None, "the dialog asks for no duration"
    assert 'min="1"' in days_input.group(0) and 'max="999"' in days_input.group(0)
    assert "required" not in days_input.group(0), "an empty duration must stay possible"
    assert "Empty = permanent" in text, "the duration's own help line is missing"
    # the scope picker: the bot-wide value, then the caller's own servers
    assert 'name="scope"' in text and "<select" in text
    assert ">All my servers</option>" in text, "the bot-wide option is missing"
    assert '<option value="SRS-1">SRS-1</option>' in text
    assert '<option value="SRS-2">SRS-2</option>' in text
    assert "SRS-B" not in text, "the picker offered a server outside the caller's scope"
    # NO token: the dialog is a form, and adding a ban is not irreversible
    assert "_confirm_token" not in text, "the ADD dialog minted a one-shot token it never spends"
    # the tokenless form's chrome: no "confirm" badge, and no sentence claiming it is irreversible
    assert '<span class="tag">options</span>' in text, "the tokenless form claims a confirmation"
    assert "cannot be undone" not in text, "the copy says the liftable write cannot be undone"


def test_the_two_ban_dialogs_build_their_fields_from_one_builder():
    """The reason and the duration are ONE pair of records — the ADD reaches the builder, not a copy."""
    player_ban = next(action for action in actions_page.PLAYER_ACTIONS if action.key == "ban")
    reason, days = actions_page.ban_dialog_fields(actions_page.BAN_ADD_ACTION)
    assert (reason["name"], days["name"]) == ("reason", "days")
    assert actions_page.ban_dialog_fields(player_ban) == (reason, days), \
        "the two ban dialogs do not draw the same reason and duration records"
    assert days["bounds"] == {"min": actions_page.BAN_DAYS_MIN, "max": actions_page.BAN_DAYS_MAX}
    assert days["required"] is False and days["type"] == "number"
    assert reason["required"] is True and reason["max"] == 80


@pytest.mark.asyncio
async def test_the_add_needs_no_confirm_token(config_dir, clean_registry, ban_bus):
    """The contrast with the LIFT: a tokenless POST of the ADD is a WRITE, not a refusal."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        reply = await http.post(ADD, data={"ucid": UCID, "reason": "cheating", "days": "",
                                           "scope": "", "_csrf_token": token_of(page)})
    assert reply.status_code == 303, reply.text
    assert len(written(ban_bus)) == 1, "the ADD refused a POST it has no reason to refuse"


# ------------------------------------------------------------- 6. the catalogs


WEBUI_MSGIDS = (
    "Add ban…", "Add a ban to the list", "Add a ban", "Add a ban?", "Add ban",
    "the ban is written to the list every installation syncs",
    "the UCID you enter below", "Player (UCID)",
    "The player's UCID, as the ban list records it — e.g. 4b1f9c22.",
    "Applies to", "All my servers", "Every server (bot-wide)",
    "One server, or every server you manage. A ban on all of them is written to the list every "
    "installation syncs.",
    "<b>The player is removed from the servers this ban covers and cannot rejoin them</b> for as "
    "long as the ban stands. Empty duration means it never expires on its own.",
    "The ban is written to the ban list every installation syncs, so it holds wherever they connect "
    "from — not only on one server.",
    "Add the ban — an empty duration is permanent",
    "Held in the DGSA global ban list, shared by every installation that syncs it",
)
MISSION_MSGIDS = (
    "'{}' is not a valid UCID.",
    "A reason is required to ban a player.",
    "The reason is longer than {} characters.",
    "Server '{}' is outside your scope.",
    "Player {} banned for {} days on {}.",
    "Player {} banned permanently on {}.",
    "Timeout while adding the ban.",
    "Failed to add the ban: {}",
)
SHIPPED = ("cn", "de", "es", "ru")


def _catalog_msgids(path: Path) -> set[str]:
    """Every msgid a ``.po`` carries, with Babel's line wrapping undone.

    A long msgid is written as an empty ``msgid ""`` followed by continuation string literals, so a
    raw search for the sentence understates the catalog. Reads the SAME escaping a PO file states.
    """
    import ast

    found: set[str] = set()
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            current = ast.literal_eval(line[len("msgid "):])
        elif current is not None and line.startswith('"'):
            current += ast.literal_eval(line)
        elif current is not None:
            found.add(current)
            current = None
    if current is not None:
        found.add(current)
    return found


@pytest.mark.parametrize("language", SHIPPED)
def test_every_webui_catalog_carries_the_add_msgids(language):
    """The source half: the new strings reach the ``webui`` catalog of every translated language."""
    carried = _catalog_msgids(REPO / "locale" / language / "LC_MESSAGES" / "webui.po")
    for msgid in WEBUI_MSGIDS:
        assert msgid in carried, f"{language} webui.po lacks {msgid!r}"


@pytest.mark.parametrize("language", SHIPPED)
def test_the_compiled_webui_catalog_resolves_the_add_msgids(language):
    """The runtime half: the recompiled ``.mo`` serves them — a stale ``.mo`` cannot carry a fresh ``.po``."""
    translations = gettext.translation("webui", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in WEBUI_MSGIDS:
        assert translations.gettext(msgid) != msgid, f"{language}: {msgid!r} still renders English"


@pytest.mark.parametrize("language", SHIPPED)
def test_every_mission_catalog_carries_the_add_msgids(language):
    """The action's own sentences live in the ``mission`` catalog (the plugin's own domain)."""
    carried = _catalog_msgids(REPO / "locale" / language / "LC_MESSAGES" / "mission.po")
    for msgid in MISSION_MSGIDS:
        assert msgid in carried, f"{language} mission.po lacks {msgid!r}"


@pytest.mark.parametrize("language", SHIPPED)
def test_the_compiled_mission_catalog_resolves_the_add_msgids(language):
    translations = gettext.translation("mission", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in MISSION_MSGIDS:
        resolved = translations.gettext(msgid)
        assert resolved != msgid, f"{language}: {msgid!r} still renders English"
        assert resolved.count("{}") == msgid.count("{}"), \
            f"{language}: {msgid!r} translation drops a placeholder: {resolved!r}"
