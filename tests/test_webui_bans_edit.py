"""The Bans page's EDIT flow: change a ban ENTRY's reason and expiration — and nothing else.

One section per acceptance criterion of the card:

* the list renders an EDIT control beside the LIFT on every NON-DGSA row, and NO edit control on a
  DGSA row (the rendering half of "DGSA is refused twice over");
* an edit changes the reason and/or the ABSOLUTE UTC expiration and leaves the entry's ``banned_at``
  (its identity) untouched, and never rewrites ``banned_by`` (a fact about who placed the ban);
* the expiration is a UTC datetime typed as text: a reason-only save posts the entry's OWN end back
  and writes the IDENTICAL ``banned_until``, a changed value lands exactly, and a blank makes the
  entry permanent (the list renders ``never``);
* an entry with AT LEAST ONE row outside the caller's set is refused WHOLE with nothing written (the
  all-or-nothing pin), while a fully-in-scope entry edits for a narrowed holder;
* a DGSA entry is refused by the ACTION (its own message) and by its dialog's route;
* every refusal writes nothing (bad UCID, missing/oversized reason, malformed expiration, an
  expiration at or before now) and the action refuses a non-web transport;
* the edit dialog's ``Expires (UTC)`` field is pre-filled with the entry's own UTC end, and the ADD
  and the player-row Ban keep their day-count field;
* the door map now covers ``/actions/ban/edit`` and its capability;
* the new msgids are in the ``webui`` catalogs (and their recompiled ``.mo``), and the action's
  sentences are in the ``mission`` catalogs.

Driven through the REAL application (the production factory, the shell, the local login backend and
a stub source) with the ban list behind a stub ServiceBus, exactly like the slice-1/2 suites this
file extends; the harness is imported from them rather than copied.
"""
from __future__ import annotations

import datetime
import gettext
import re
import types
from pathlib import Path

import pytest

from core.actions import ActionContext
from plugins.mission.actions import (
    BAN_PERMANENT_UNTIL, BANS_CONSOLE_ONLY, DGSA_BANNED_BY, edit_ban, parse_ban_until,
)
from test_webui_bans_add import two_servers  # noqa: F401 - the N-row cluster, reused on purpose
from test_webui_bans_page import (  # noqa: F401 - the ban harness, reused on purpose
    LATER, NEVER, NOW, _console_registrar, _row, _transport_context, ban_bus, bans_page_html,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures reused on purpose
    build, clean_registry, client, config_dir, fleet, recording_bot, sign_in, token_of,
)
from services.webservice.pages import actions as actions_page
from services.webservice.readmodels.bans import expires_utc

REPO = Path(__file__).resolve().parent.parent
EDIT = actions_page.BAN_EDIT_PATH
EDIT_DIALOG = EDIT + actions_page.CONFIRM_SUFFIX
LIFT = actions_page.BAN_LIFT_PATH
LIFT_DIALOG = LIFT + actions_page.CONFIRM_SUFFIX
#: A REAL UCID: 32 characters, alphanumeric, lowercase (``core.utils.is_ucid``).
UCID = "4b1f9c22" * 4
OTHER = "deadbeef" * 4

#: The page harness's own default end (``_row``'s ``banned_until`` is ``LATER``), as the field's
#: value: posting it back is the reason-only save that must NOT move the entry. The SECONDS are part
#: of the layout (a days-created ban stores them), so the field's value carries them too.
ENTRY_UNTIL_STR = LATER.strftime("%Y-%m-%d %H:%M:%S")
#: A changed, future absolute end — the value a real operator types. Nonzero seconds, so a value that
#: loses them is a visible drift.
FUTURE_STR = "2030-01-01 12:00:30"
FUTURE = datetime.datetime(2030, 1, 1, 12, 0, 30)
#: A malformed stamp and a stamp before ``NOW`` (both must be refused).
PAST_STR = "2000-01-01 00:00:00"
#: A FIXED UTC wall clock injected into the parser, so the date-only fill and the past check are
#: deterministic: microseconds are present on purpose, so a date-only fill (which carries whole
#: seconds) is strictly BEFORE the reference and never reads as "now".
FIXED_NOW = datetime.datetime(2026, 10, 20, 9, 30, 45, 123456)


async def edit(http, *, ucid=UCID, banned_at=None, reason="cheating", until=ENTRY_UNTIL_STR,
               dialog=True):
    """Run the WHOLE edit: the row's control, the dialog, then the dialog's own write."""
    key = NOW.isoformat() if banned_at is None else banned_at
    page = await bans_page_html(http)
    if dialog:
        reply = await http.post(EDIT_DIALOG, data={"ucid": ucid, "banned_at": key,
                                                   "_csrf_token": token_of(page)})
        assert reply.status_code == 200, reply.text
    return await http.post(EDIT, data={"ucid": ucid, "banned_at": key, "reason": reason,
                                       "until": until, "_csrf_token": token_of(page)})


def written(bus) -> list[dict]:
    """The rows the edit asked the bus to write, in order."""
    return list(bus.bans_written)


def snapshot(bus) -> list[dict]:
    """The row set as it stands, so a refusal can be shown to have changed NOTHING."""
    return [dict(row) for row in bus.rows]


def notice(html: str) -> str:
    """The one-shot notice's message — the sentence a refused write left for the page it lands on."""
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1).strip() if match else ""


# ------------------------------------------------------------- 1. the declarations and the controls


def test_the_edit_is_a_row_control_and_the_add_is_the_page_control():
    """One declaration each, and the two are distinct: a row offers no ADD, a page no EDIT."""
    assert actions_page.BAN_EDIT_ACTION.qualname == "edit_ban"
    assert actions_page.BAN_EDIT_ACTION.path == "/actions/ban/edit"
    assert actions_page.BAN_EDIT_ACTION.capability == "players.edit_ban"
    assert actions_page.BAN_EDIT_ACTION.form is True, "the edit must reach its own form"
    assert actions_page.BAN_EDIT_ACTION.confirm is False, "an edit mints no one-shot token"
    assert actions_page.BAN_EDIT_ACTION.danger is False, "the mockup tints the edit button not at all"
    assert actions_page.BAN_EDIT_ACTION in actions_page.BAN_ACTIONS
    assert actions_page.BAN_EDIT_ACTION not in actions_page.PAGE_CONTROLS["bans"]
    assert actions_page.PAGE_CONTROLS["bans"] == (actions_page.BAN_ADD_ACTION,)


@pytest.mark.asyncio
async def test_the_list_offers_edit_beside_lift_on_a_row(config_dir, clean_registry, ban_bus):
    """A non-DGSA row carries BOTH controls, EDIT first, and each posts to its own dialog."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert html.count(f'action="{EDIT_DIALOG}"') == 1, "the row offers no edit control"
    assert html.count(f'action="{LIFT_DIALOG}"') == 1, "the row lost its lift control"
    # the row is a GLYPH STRIP: the controls are pinned by their tooltips, and EDIT is drawn first
    edit_title = 'title="Edit the reason or the expiration of the ban on Alpha"'
    lift_title = 'title="Lift — remove this ban from the list"'
    assert edit_title in html, "the edit control lost its tooltip"
    assert html.index(edit_title) < html.index(lift_title), "the mockup draws Edit before Lift"
    assert "Edit the reason or the expiration of the ban on Alpha" in html

@pytest.mark.asyncio
async def test_a_dgsa_row_offers_no_edit_control(config_dir, clean_registry, ban_bus):
    """A DGSA row keeps its LIFT and carries NO edit control — the control is absent, not disabled."""
    ban_bus.rows = [_row(UCID, name="Global", server_name=None, banned_by=DGSA_BANNED_BY,
                         reason="DGSA: cheating"),
                    _row(OTHER, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get("/bans?dgsa=1")).text
    assert "Global" in html and "Alpha" in html
    # the DGSA row is one of the two; only the ordinary row may offer an edit
    assert html.count(f'action="{EDIT_DIALOG}"') == 1, "a DGSA row offered an edit control"
    assert html.count(f'action="{LIFT_DIALOG}"') == 2, "a DGSA row lost its lift"


def test_the_edit_draws_a_utc_text_field_not_the_day_count_record():
    """The EDIT's expiration is its OWN record — a UTC text field, never the shared day-count box.

    The ADD and the player-row Ban keep the day-count record (``ban_dialog_fields``); the EDIT reaches
    ``ban_until_field``, whose name (``until``) is the action's own parameter and whose kind is text.
    """
    field = actions_page.ban_until_field()
    assert field["name"] == "until", "the edit's field does not map to the action's parameter"
    assert field["type"] == "text", "the edit's expiration must be typed, not a number box"
    assert field["bounds"] is None, "the UTC field must not carry the day-count bounds"
    # the ADD and the player-row Ban keep the shared day-count record
    add_reason, add_days = actions_page.ban_dialog_fields(actions_page.BAN_ADD_ACTION)
    assert add_days == actions_page.ban_days_field(), "the ADD lost the shared day-count record"
    assert (add_days["name"], add_days["type"]) == ("days", "number")
    assert add_days["bounds"] == {"min": actions_page.BAN_DAYS_MIN, "max": actions_page.BAN_DAYS_MAX}
    # the EDIT's inputs are the reason + the until field, not the days record
    row = _row(UCID, name="Alpha", reason="cheating")
    reason, expires = actions_page.ban_edit_inputs(actions_page.BAN_EDIT_ACTION, row)
    assert (reason["name"], reason["required"], reason["max"]) == ("reason", True, 80)
    assert (expires["name"], expires["type"]) == ("until", "text")


# ------------------------------------------------------------- 2. the write


@pytest.mark.asyncio
async def test_a_reason_only_edit_writes_the_entrys_own_end_back_unchanged(config_dir, clean_registry,
                                                                          ban_bus):
    """Posting the entry's OWN end back (the pre-filled field) must NOT move the expiry or the stamp.

    The entry is a days-created ban, so its stored ``banned_until`` carries SECONDS. The dialog
    pre-fills the entry's absolute end and a reason-only save posts exactly that value back, so the
    stored ``banned_until`` and ``banned_at`` must come out BYTE-IDENTICAL — to the second. A
    minute-precision field would truncate the seconds and move the end on a save that changed nothing.
    """
    end = datetime.datetime(2027, 6, 1, 12, 0, 45)          # seconds matter
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1", banned_by="mod",
                         reason="cheating", banned_until=end)]
    posted = expires_utc(ban_bus.rows[0])                   # exactly what the dialog pre-fills
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, reason="ban evasion", until=posted)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    row = ban_bus.rows[0]
    assert row["reason"] == "ban evasion"
    assert row["banned_until"] == end, "a reason-only edit moved the expiration"
    assert row["banned_at"] == NOW, "the edit moved the entry in time"
    assert row["banned_by"] == "mod", "the edit rewrote who placed the ban"
    assert "ban evasion" in html


@pytest.mark.asyncio
async def test_an_edit_changes_the_expiration_to_the_exact_utc_value(config_dir, clean_registry,
                                                                     ban_bus):
    """A changed UTC datetime lands EXACTLY — the entry keeps its identity."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, reason="cheating", until=FUTURE_STR)
    assert reply.status_code == 303, reply.text
    row = ban_bus.rows[0]
    assert row["banned_until"] == FUTURE, f"the expiry is not the typed value: {row['banned_until']}"
    assert row["banned_at"] == NOW


@pytest.mark.asyncio
async def test_an_empty_expiration_makes_the_ban_permanent_and_the_list_renders_never(
        config_dir, clean_registry, ban_bus):
    """A blank expiration is the permanent sentinel, and the list renders the word ``never``."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until="")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert ban_bus.rows[0]["banned_until"].year == 9999, "the permanent sentinel was not written"
    assert '<span class="tag">never</span>' in html
    assert "9999" not in html


# ------------------------------------------------------------- 3. the all-or-nothing scope pin


@pytest.mark.asyncio
async def test_a_partially_owned_entry_is_refused_whole_with_nothing_written(
        config_dir, clean_registry, ban_bus, two_servers):
    """One ban covers SRS-1 and SRS-B; the Host A manager holds only SRS-1 ⇒ refused, nothing moves."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1"),
                    _row(UCID, name="Alpha", server_name="SRS-B")]
    before = snapshot(ban_bus)
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await edit(http, reason="rewritten")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], "a partially owned entry was edited"
    assert snapshot(ban_bus) == before, "a refused edit changed the rows"
    assert "No active ban" in html, "the out-of-scope refusal must read as the missing-ban one"


# --------------------------------------------- 3b. the refusal that must not disclose existence


@pytest.mark.asyncio
async def test_the_out_of_scope_refusal_reads_the_same_as_a_missing_ban(
        config_dir, clean_registry, ban_bus, two_servers):
    """An entry entirely outside the caller's set and an identity naming no entry give ONE message.

    A distinguishable pair would let a holder of ``players.edit_ban`` learn that a ban stands outside
    their scope (message A) or does not (message B), so both refusals carry the SAME sentence — the
    wording the LIFT path and the dialog route already answer both cases with.

    The discriminating probe is the notice, not the code path: the two runs differ ONLY in whether the
    bus holds a row on a foreign server, so equal notices mean the messages cannot be told apart.
    """
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        # a) a ban that EXISTS, but only on a server the caller does not manage
        ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-B")]
        before = snapshot(ban_bus)
        await edit(http, ucid=UCID, dialog=False)
        outside = notice(await bans_page_html(http))
        assert written(ban_bus) == [], "an out-of-scope entry was edited"
        assert snapshot(ban_bus) == before, "a refused out-of-scope edit changed the rows"
        # b) an identity that names NO ban at all
        ban_bus.rows = []
        await edit(http, ucid=UCID, dialog=False)
        missing = notice(await bans_page_html(http))
    assert written(ban_bus) == []
    assert outside and missing, "a refusal left no notice to compare"
    assert outside == missing, f"the two refusals disclose each other: {outside!r} vs {missing!r}"


@pytest.mark.asyncio
async def test_a_crafted_scope_field_cannot_move_the_ban(config_dir, clean_registry, ban_bus):
    """EDIT is reason and expiration ONLY: a posted scope/server_name is not read, so nothing moves."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        reply = await http.post(EDIT, data={"ucid": UCID, "banned_at": NOW.isoformat(),
                                            "reason": "rewritten", "until": ENTRY_UNTIL_STR,
                                            "scope": "SRS-B", "server_name": "SRS-B",
                                            "_csrf_token": token_of(page)})
    assert reply.status_code == 303, reply.text
    rows = written(ban_bus)
    assert [row["server_name"] for row in rows] == ["SRS-1"], \
        "a crafted scope field moved the ban to another server"


@pytest.mark.asyncio
async def test_a_fully_in_scope_entry_edits_for_a_narrowed_holder(config_dir, clean_registry,
                                                                  ban_bus, two_servers):
    """The same ban, held entirely by the caller: every row is rewritten, sharing one identity."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1"),
                    _row(UCID, name="Alpha", server_name="SRS-2")]
    app = build(config_dir, two_servers)
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await edit(http, reason="teamkilling")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    rows = written(ban_bus)
    assert sorted(row["server_name"] for row in rows) == ["SRS-1", "SRS-2"], \
        "the edit did not cover the entry's full row set"
    assert {row["reason"] for row in rows} == {"teamkilling"}, "the rows no longer group as one entry"
    assert {row["banned_at"] for row in rows} == {NOW}, "the rows lost their shared identity"
    assert html.count('title="Lift — remove this ban from the list"') == 1, \
        "the entry split into more than one row"


# ------------------------------------------------------------- 4. DGSA is refused twice over


@pytest.mark.asyncio
async def test_a_dgsa_entry_is_refused_by_the_action(config_dir, clean_registry, ban_bus):
    """A crafted POST on a DGSA entry reaches the action and is refused with its own message."""
    ban_bus.rows = [_row(UCID, name="Global", server_name=None, banned_by=DGSA_BANNED_BY,
                         reason="DGSA: aimbot")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, reason="rewritten", dialog=False)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], "a DGSA entry was edited"
    assert snapshot(ban_bus) == before
    assert "DGSA ban cannot be edited" in html


@pytest.mark.asyncio
async def test_the_edit_dialog_refuses_a_dgsa_entry(config_dir, clean_registry, ban_bus):
    """The dialog's route refuses a DGSA entry outright — the door behind the omitted control."""
    ban_bus.rows = [_row(UCID, name="Global", server_name=None, banned_by=DGSA_BANNED_BY)]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        reply = await http.post(EDIT_DIALOG, data={"ucid": UCID, "banned_at": NOW.isoformat(),
                                                   "_csrf_token": token_of(page)})
    assert reply.status_code == 403, reply.text


@pytest.mark.asyncio
async def test_the_edit_dialog_is_a_tokenless_form(config_dir, clean_registry, ban_bus):
    """The dialog posts to the EDIT, mints NO one-shot token, and wears the reversible chrome."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1", reason="cheating")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(EDIT_DIALOG, data={"ucid": UCID, "banned_at": NOW.isoformat(),
                                                    "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert f'action="{EDIT}"' in text, "the dialog does not post to the edit"
    assert "Edit the ban on Alpha?" in text, "the heading lost the resolved player"
    assert "Save ban" in text
    assert "_confirm_token" not in text, "the edit dialog minted a one-shot token it never spends"
    assert '<span class="tag">options</span>' in text, "the tokenless form claims a confirmation"
    assert "cannot be undone" not in text
    assert "Alpha · " + UCID in text, "the target line does not name the resolved player and ucid"


@pytest.mark.asyncio
async def test_the_edit_dialog_prefills_the_reason_and_the_entries_own_utc_end(config_dir,
                                                                              clean_registry,
                                                                              ban_bus):
    """Both fields arrive PRE-FILLED from the entry — the reason, and the entry's OWN UTC end."""
    until = datetime.datetime(2027, 6, 1, 12, 0, 30)
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1", reason="cheating",
                         banned_until=until)]
    expected = expires_utc(ban_bus.rows[0])
    assert expected == "2027-06-01 12:00:30", f"the field must carry the entry's own end, not {expected}"
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(EDIT_DIALOG, data={"ucid": UCID, "banned_at": NOW.isoformat(),
                                                    "_csrf_token": token_of(page)})
    text = dialog.text
    reason = re.search(r'<input type="text" id="reason-dialog"[^>]*>', text)
    expires = re.search(r'<input type="text" id="until-dialog"[^>]*>', text)
    assert reason is not None and 'value="cheating"' in reason.group(0)
    assert expires is not None and f'value="{expected}"' in expires.group(0), \
        "the edit form does not pre-fill the entry's own UTC end"


@pytest.mark.asyncio
async def test_a_permanent_ban_prefills_no_expiration(config_dir, clean_registry, ban_bus):
    """A ban that never expires pre-fills the expiration EMPTY — the field's own permanent value."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1", banned_until=NEVER)]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(EDIT_DIALOG, data={"ucid": UCID, "banned_at": NOW.isoformat(),
                                                    "_csrf_token": token_of(page)})
    expires = re.search(r'<input type="text" id="until-dialog"[^>]*>', dialog.text)
    assert expires is not None
    assert "value=" not in expires.group(0), "a permanent ban pre-filled an expiration"


# ------------------------------------------------------------- 5. the refusals


@pytest.mark.asyncio
@pytest.mark.parametrize("ucid", ["not-a-ucid", "4B1F9C22" * 4, ""])
async def test_a_bad_ucid_is_refused_with_nothing_written(config_dir, clean_registry, ban_bus, ucid):
    """The check the Discord ``/dcs ban`` uses: a 32-character lowercase alphanumeric ucid."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, ucid=ucid, dialog=False)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [] and snapshot(ban_bus) == before
    assert "is not a valid UCID" in html


@pytest.mark.asyncio
async def test_an_empty_or_oversized_reason_is_refused_with_nothing_written(
        config_dir, clean_registry, ban_bus):
    """A reason is required and bounded by the ban list's own cap — nothing is written on either."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        empty = await edit(http, reason="   ")
        html = await bans_page_html(http)
        long = await edit(http, reason="x" * 81)
    assert empty.status_code == 303 and long.status_code == 303
    assert written(ban_bus) == [] and snapshot(ban_bus) == before
    assert "A reason is required" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("until", ["abc", "2020-13-01 00:00", "01-01-2020 00:00",
                                   "2030/01/01 12:00", "2030-01", "2030-01-01 12:00:00 junk",
                                   "2030-01-01 25", "2030-01-01 12:60", "2030-01-01 12:00:99",
                                   "2030-02-30"])
async def test_a_malformed_expiration_is_refused_with_nothing_written(config_dir, clean_registry,
                                                                      ban_bus, until):
    """A stamp that fits NO accepted shape is a TYPED refusal — and no row is written.

    The accepted shapes are ``YYYY-MM-DD HH:MM:SS`` and the shorter ones that fill the missing parts
    (:func:`parse_ban_until`); anything else — a bad month/day/hour/minute/second, a bare ``YYYY-MM``,
    trailing junk — is refused rather than silently zeroed or accepted.
    """
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until=until)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], f"a malformed expiration ({until!r}) still reached the bus"
    assert snapshot(ban_bus) == before, f"a malformed expiration ({until!r}) wrote a row"
    assert "Expires must" in html


# ------------------------------------- 5b. the lenient parser: the accepted shapes and the refusals


@pytest.mark.parametrize("text, expected", [
    # the exact shape: to the second
    ("2029-03-04 05:06:07", datetime.datetime(2029, 3, 4, 5, 6, 7)),
    # minutes: the seconds become 00
    ("2029-03-04 05:06", datetime.datetime(2029, 3, 4, 5, 6, 0)),
    # the hour alone: minutes AND seconds become 00
    ("2029-03-04 05", datetime.datetime(2029, 3, 4, 5, 0, 0)),
    # the date alone: the CURRENT wall clock (FIXED_NOW's H:M:S) on that date
    ("2029-03-04", datetime.datetime(2029, 3, 4, 9, 30, 45)),
    # ONE trim, and the optional trailing UTC / Z
    ("  2029-03-04 05:06:07  ", datetime.datetime(2029, 3, 4, 5, 6, 7)),
    ("2029-03-04 05:06:07 UTC", datetime.datetime(2029, 3, 4, 5, 6, 7)),
    ("2029-03-04 05:06Z", datetime.datetime(2029, 3, 4, 5, 6, 0)),
    ("2029-03-04 05 UTC", datetime.datetime(2029, 3, 4, 5, 0, 0)),
])
def test_the_accepted_expiration_shapes_fill_the_missing_parts(text, expected):
    """Every accepted shape parses to the right instant against a FIXED now — the fill is exact."""
    parsed, refusal = parse_ban_until(text, now=FIXED_NOW)
    assert refusal == "", f"{text!r} was refused: {refusal}"
    assert parsed == expected, f"{text!r} parsed to {parsed}, not {expected}"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_expiration_is_the_permanent_sentinel(blank):
    """Blank / whitespace / absent is the permanent sentinel — unchanged, and never a refusal."""
    parsed, refusal = parse_ban_until(blank, now=FIXED_NOW)
    assert (parsed, refusal) == (BAN_PERMANENT_UNTIL, "")


def test_an_aware_now_is_read_as_utc():
    """An injected AWARE now is converted to UTC before the fill and the past check read it."""
    aware = datetime.datetime(2026, 10, 20, 11, 30, 45,
                              tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    parsed, refusal = parse_ban_until("2029-03-04", now=aware)
    assert refusal == ""
    assert parsed == datetime.datetime(2029, 3, 4, 9, 30, 45), \
        "the date-only fill did not read the injected aware now as UTC"


@pytest.mark.parametrize("text", ["abc", "2020-13-01 00:00", "01-01-2020 00:00",
                                  "2030/01/01 12:00", "2030-01", "2030-01-01 12:00:00 junk",
                                  "2030-01-01 25", "2030-01-01 12:60", "2030-01-01 12:00:99",
                                  "2030-02-30",
                                  # a NON-PADDED field, a tab and a double space are NOT a shape the
                                  # field spells out: refused, never a lenient strptime read
                                  "2030-1-1", "2030-01-01\t12:00", "2030-01-01  12:00",
                                  "2030-01-01 12:"])
def test_the_parser_refuses_every_unaccepted_shape(text):
    """A text the pattern does not match whole is a TYPED refusal — ``(None, sentence)``, fixed now."""
    parsed, refusal = parse_ban_until(text, now=FIXED_NOW)
    assert parsed is None, f"{text!r} was accepted as {parsed}"
    assert refusal, f"{text!r} was refused without a sentence"


@pytest.mark.parametrize("text", ["2026-10-20 09:30:45", "2026-10-20 09:30:44",
                                  "2026-10-19 23:59:59", "2026-10-20"])
def test_the_parser_refuses_a_past_or_now_stamp_against_a_fixed_now(text):
    """At or before the injected now is refused — including a date-only value that fills to now."""
    parsed, refusal = parse_ban_until(text, now=FIXED_NOW)
    assert parsed is None, f"{text!r} was accepted as {parsed}"
    assert "future" in refusal


#: The accepted suffix tokens: ONE trailing ``Z`` or ``UTC``. The separator before ``UTC`` is at most
#: one space (the ``UTC``-glued form is the zero-space case), and ``Z`` is adjacent by convention.
_SUFFIX_TOKENS = ("Z", " UTC", "UTC")


@pytest.mark.parametrize("suffix", _SUFFIX_TOKENS)
@pytest.mark.parametrize("stamp, expected", [
    # the full shape: to the second
    ("2029-03-04 05:06:07", datetime.datetime(2029, 3, 4, 5, 6, 7)),
    # HH:MM: the seconds become 00
    ("2029-03-04 05:06", datetime.datetime(2029, 3, 4, 5, 6, 0)),
    # HH alone: minutes AND seconds become 00
    ("2029-03-04 05", datetime.datetime(2029, 3, 4, 5, 0, 0)),
    # the date alone: the current wall clock (FIXED_NOW's H:M:S) on that date
    ("2029-03-04", datetime.datetime(2029, 3, 4, 9, 30, 45)),
])
def test_one_suffix_token_is_accepted_on_every_stamp_shape(stamp, expected, suffix):
    """EXACTLY ONE trailing suffix (``Z`` / `` UTC``) is peeled on every accepted stamp shape."""
    parsed, refusal = parse_ban_until(stamp + suffix, now=FIXED_NOW)
    assert refusal == "", f"{stamp + suffix!r} was refused: {refusal}"
    assert parsed == expected, f"{stamp + suffix!r} parsed to {parsed}, not {expected}"


#: The doubled / malformed suffix zone: ``Z`` and ``UTC`` are redundant, so a SECOND token — glued or
#: spaced, in either order — and a TAB used as the separator are all refused.
_DOUBLED_SUFFIXES = ("ZUTC", "Z UTC", "ZZ", "UTC UTC", "\tUTC")


@pytest.mark.parametrize("suffix", _DOUBLED_SUFFIXES)
@pytest.mark.parametrize("stamp", ["2029-03-04 05:06:07", "2029-03-04 05:06", "2029-03-04 05",
                                   "2029-03-04"])
def test_a_doubled_or_tab_separated_suffix_is_refused(stamp, suffix):
    """A doubled suffix token, or a TAB separator, is a TYPED refusal — nothing written."""
    parsed, refusal = parse_ban_until(stamp + suffix, now=FIXED_NOW)
    assert parsed is None, f"{stamp + suffix!r} was accepted as {parsed}"
    assert refusal, f"{stamp + suffix!r} was refused without a sentence"


@pytest.mark.asyncio
async def test_a_minute_precision_edit_zeroes_the_seconds(config_dir, clean_registry, ban_bus):
    """``HH:MM`` reaches the DB as ``:00`` seconds — the lenient shape lands exactly."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until="2030-01-01 12:00")
    assert reply.status_code == 303, reply.text
    assert ban_bus.rows[0]["banned_until"] == datetime.datetime(2030, 1, 1, 12, 0, 0)


@pytest.mark.asyncio
async def test_an_hour_precision_edit_zeroes_minutes_and_seconds(config_dir, clean_registry, ban_bus):
    """``HH`` reaches the DB as ``:00:00`` — minutes AND seconds zeroed."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until="2030-01-01 12")
    assert reply.status_code == 303, reply.text
    assert ban_bus.rows[0]["banned_until"] == datetime.datetime(2030, 1, 1, 12, 0, 0)


@pytest.mark.asyncio
async def test_a_date_only_edit_takes_the_current_time_on_that_date(config_dir, clean_registry,
                                                                    ban_bus):
    """A date alone bans until that date at the CURRENT wall clock — the written time is now's."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    before = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until="2030-01-01")
    after = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
    assert reply.status_code == 303, reply.text
    written = ban_bus.rows[0]["banned_until"]
    assert written.date() == datetime.date(2030, 1, 1)
    # the TIME OF DAY is now's — compared on the clock alone (the date is the typed one)
    assert before.time() <= written.time() <= after.time(), \
        f"the date-only fill is not the current time: {written}"


@pytest.mark.asyncio
async def test_a_past_expiration_is_refused_with_nothing_written(config_dir, clean_registry, ban_bus):
    """An expiration at or before now is a TYPED refusal — an expiry in the past IS a lift."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until=PAST_STR)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], "a past expiration still reached the bus"
    assert snapshot(ban_bus) == before, "a past expiration wrote a row"
    assert "in the future" in html


@pytest.mark.asyncio
async def test_a_trailing_utc_marker_is_accepted(config_dir, clean_registry, ban_bus):
    """A trailing `` UTC`` / ``Z`` is stripped — the same instant is written."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until=FUTURE_STR + " UTC")
    assert reply.status_code == 303, reply.text
    assert ban_bus.rows[0]["banned_until"] == FUTURE, \
        "a trailing UTC marker was not accepted/stripped"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["ZUTC", "Z UTC", "ZZ", "UTC UTC", "\tUTC"])
async def test_a_doubled_suffix_is_refused_with_nothing_written(config_dir, clean_registry,
                                                                ban_bus, suffix):
    """``Z`` and ``UTC`` are redundant: a second token (glued or spaced) writes NOTHING."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    before = snapshot(ban_bus)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, until=FUTURE_STR + suffix)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == [], f"a doubled suffix ({suffix!r}) still reached the bus"
    assert snapshot(ban_bus) == before, f"a doubled suffix ({suffix!r}) wrote a row"
    assert "Expires must" in html


@pytest.mark.asyncio
async def test_an_entry_with_no_active_ban_is_refused_with_nothing_written(config_dir,
                                                                           clean_registry, ban_bus):
    """An identity that names no active entry is a typed refusal, and the bus is never written."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, ucid=OTHER, banned_at=NOW.isoformat(), dialog=False)
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    assert written(ban_bus) == []
    assert "No active ban" in html


@pytest.mark.asyncio
async def test_the_edit_refuses_a_non_web_transport(ban_bus):
    """MCP / REST carry no scope: a hand-built or service context is refused before any write."""
    ban_bus.rows = [_row(UCID, name="Alpha", server_name="SRS-1")]
    for ctx in (_transport_context(""), ActionContext.from_service(
            types.SimpleNamespace(node=types.SimpleNamespace()))):
        result = await edit_ban(ctx, UCID, NOW.isoformat(), "cheating")
        assert result.success is False
        assert result.message == BANS_CONSOLE_ONLY
    assert written(ban_bus) == [], "a non-console caller reached the ban list"


# ------------------------------------------------------------- 6. the door map


def test_the_door_map_covers_the_edit_path_and_its_capability():
    """Both halves of the edit's surface — its POST and its dialog — carry the declared capability."""
    registrar = _console_registrar()
    declared = {EDIT: actions_page.BAN_EDIT_CAPABILITY,
                EDIT_DIALOG: actions_page.BAN_EDIT_CAPABILITY}
    seen = {getattr(route, "path", "") for route in registrar.routes}
    for path, capability in declared.items():
        assert path in seen, f"the declared edit path {path} is not registered"
        assert registrar.capability_for(path=path) == capability
    carrying = {path for path in seen
                if registrar.capability_for(path=path) == actions_page.BAN_EDIT_CAPABILITY}
    assert carrying == set(declared), "an undeclared path carries the edit capability"
    # and the edit capability is NOT the add's or the lift's
    assert actions_page.BAN_EDIT_CAPABILITY not in (
        actions_page.BAN_ADD_CAPABILITY, actions_page.BAN_LIFT_CAPABILITY)


def test_the_plugins_dgsa_marker_is_the_cloud_plugins_own_name():
    """``DGSA_BANNED_BY`` must be the name the cloud plugin declares, or the refusal dies silently.

    The plugin keeps its own copy of the word for the same reason the console does (it may not import
    a plugin module); ``core/plugin.py`` derives ``plugin_name`` from the plugin's OWN module path, so
    that expression — read off the real class — is the tree's source of truth. The two copies (the
    plugin's and the console's) are pinned equal here, so renaming ``plugins/cloud`` fails both.
    """
    from plugins.cloud.commands import Cloud
    from services.webservice.readmodels.bans import DGSA_BANNED_BY as CONSOLE_MARKER

    declared = Cloud.__module__.split(".")[-2]
    assert DGSA_BANNED_BY == declared
    assert CONSOLE_MARKER == declared


# ------------------------------------------------------------- 7. the catalogs


def test_the_two_layers_agree_on_the_edit_field_format():
    """The EDIT field's layout is ONE value: the readmodel that fills it and the action that parses it.

    They sit in different layers (the console's read model vs. the mission plugin's action), so
    neither imports the other; this pin is what keeps a change to one from silently drifting the other.
    """
    from plugins.mission import actions as mission_actions
    from services.webservice.readmodels import bans as bans_readmodel

    assert bans_readmodel.UNTIL_FORMAT == mission_actions.BAN_UNTIL_FORMAT == "%Y-%m-%d %H:%M:%S"


WEBUI_MSGIDS = (
    "Edit…", "Edit ban", "Edit the ban on {player}?",
    "Edit the reason or the expiration of the ban on {player}",
    "Edit the ban on {player}",
    "only the reason and the expiration can change",
    "Only the reason and the expiration can change. The player, the UCID and the scope stay: a "
    "different player is a different ban, and a ban that covers other servers is lifted and re-added "
    "instead of moved.",
    "Save ban", "Save the ban on {player}",
    "A DGSA ban cannot be edited — it can only be lifted here.",
    "Expires (UTC)",
    "A UTC date/time: YYYY-MM-DD HH:MM:SS, or less — HH:MM, HH, or the date alone (the current "
    "time that day). Empty = permanent.",
)
MISSION_MSGIDS = (
    "The ban could not be identified — open it from the list.",
    "No active ban for {} in your scope.",
    "A DGSA ban cannot be edited — it can only be lifted here.",
    "Ban on {} updated.",
    "Timeout while editing the ban.",
    "Failed to edit the ban: {}",
    "Expires must be a UTC date/time such as YYYY-MM-DD HH:MM:SS — leave it empty for a permanent "
    "ban.",
    "Expires must be in the future — a past date would lift the ban. Leave it empty for a permanent "
    "ban.",
)
SHIPPED = ("cn", "de", "es", "ru")


def _catalog_msgids(path: Path) -> set[str]:
    """Every msgid a ``.po`` carries, with Babel's line wrapping undone."""
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
def test_every_webui_catalog_carries_the_edit_msgids(language):
    """The source half: the new strings reach the ``webui`` catalog of every translated language."""
    carried = _catalog_msgids(REPO / "locale" / language / "LC_MESSAGES" / "webui.po")
    for msgid in WEBUI_MSGIDS:
        assert msgid in carried, f"{language} webui.po lacks {msgid!r}"


@pytest.mark.parametrize("language", SHIPPED)
def test_the_compiled_webui_catalog_resolves_the_edit_msgids(language):
    """The runtime half: the recompiled ``.mo`` serves them — a stale ``.mo`` cannot carry a fresh ``.po``."""
    translations = gettext.translation("webui", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in WEBUI_MSGIDS:
        assert translations.gettext(msgid) != msgid, f"{language}: {msgid!r} still renders English"


@pytest.mark.parametrize("language", SHIPPED)
def test_every_mission_catalog_carries_the_edit_msgids(language):
    """The action's own sentences live in the ``mission`` catalog (the plugin's own domain)."""
    carried = _catalog_msgids(REPO / "locale" / language / "LC_MESSAGES" / "mission.po")
    for msgid in MISSION_MSGIDS:
        assert msgid in carried, f"{language} mission.po lacks {msgid!r}"


@pytest.mark.parametrize("language", SHIPPED)
def test_the_compiled_mission_catalog_resolves_the_edit_msgids(language):
    translations = gettext.translation("mission", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in MISSION_MSGIDS:
        resolved = translations.gettext(msgid)
        assert resolved != msgid, f"{language}: {msgid!r} still renders English"
        assert resolved.count("{}") == msgid.count("{}"), \
            f"{language}: {msgid!r} translation drops a placeholder: {resolved!r}"
