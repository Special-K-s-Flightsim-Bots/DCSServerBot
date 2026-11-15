"""The Players page: the database directory, with the live roster one link away.

One section per acceptance criterion of the card:

* the ROUTE is registered and gated — ``/players`` answers 200 for a role holding ``players.view``
  and 403 for one without it, driven through the real application (a template render would prove
  nothing about ``_core_router()``);
* the SHOW switch picks the SOURCE: ``?show=online`` renders the live roster (a row only the
  in-memory servers carry), the three directory views render the database table (a row only the read
  carries);
* the restriction views are asked for at the READ (``?show=`` travels to it as the state), the page
  renders the rows it was served, and the total and pager describe that filtered set; a view name the
  page does not know falls back to the directory;
* a page number past the end lands on the LAST page, never on an empty table;
* the switch's links and the search form carry each other (a search typed inside ``Banned`` stays
  inside it);
* the STATE cell draws every restriction, ban before watch, and a dash for a clean player;
* a leading STATUS cell marks who is flying — ``dot run`` + Online / ``dot stop`` + Offline, always
  the word beside the dot — matched on the UCID against the in-memory roster;
* the row carries the MODERATION STRIP (Ban / Lift ban, Watch / Unwatch) and nothing that needs a
  live session; the strip is built from the same scoped rows, so a player outside the caller's scope
  has no control and a crafted POST naming one is the bare 404;
* the UCID is the player's key: the row shows one and carries NO per-row UCID count;
* the player's NAME is the door to ``/players/<ucid>``, and every control's target rides the BODY.

The directory read is stubbed at the page's own seam (``pages/players.read_action``): the SQL and the
scope rule are card 1's and are pinned by its own suite, so what this file pins is the PAGE.
"""
from __future__ import annotations

import datetime
import re
import types

import pytest

from services.webservice.pages import actions as actions_page
from services.webservice.pages import players as players_page
from test_webui_write_message import (  # noqa: F401 - the console's write harness
    UCID_A, build, client, config_dir, forms_for, message_fleet, sign_in,
)
from test_webui_write_pause import clean_registry  # noqa: F401 - the action registry fixture
from test_webui_watch_actions import audit_bot, csrf, notice, store, watch_bus  # noqa: F401

#: the directory rows the stub read answers with — one per state the page draws. ``DIRK`` is the
#: roster's own UCID, so that row is ONLINE while the others are not.
DIRK = UCID_A
REDEYE = "3e9a70c15d8b42f6a0c3e59b7d14f286"
GHOST = "b6d1f48209ce735af1d0b62c8e5a4719"
MOSQUITO = "7b2f08d4c6a19e35f0d7b2418c6e9a50"
CLEAN = "0c7d54ab19e3f682bd04e17c9a2f5d63"


def _entry(name, ucid, *, ucids=None, discord_id=-1, last_seen=None,
           playtime=None, banned=False, watched=False, vip=False):
    """One row of the directory read's ``data["rows"]``, in the action's own shape."""
    keys = tuple(ucids or (ucid,))
    return {"player_key": str(discord_id) if discord_id >= 0 else ucid, "names": [name],
            "ucids": list(keys), "ucid": ucid, "discord_id": discord_id,
            "first_seen": datetime.datetime(2024, 1, 1, 8, 0),
            "last_seen": last_seen or datetime.datetime(2026, 10, 30, 11, 47),
            "playtime": playtime if playtime is not None else 657660,
            "banned": banned, "watched": watched, "vip": vip}


def directory_rows():
    """Four players, one per state the State cell must draw."""
    return [
        _entry("Dirk", DIRK, ucids=(DIRK, "d1e5c0de0000000000000000000000bb"), discord_id=4711),
        _entry("Redeye", REDEYE, discord_id=4712, banned=True, watched=True),
        _entry("Thunder_84", GHOST, discord_id=4713, banned=True),
        _entry("Mosquito-7", MOSQUITO, watched=True),
        _entry("Kolya", CLEAN, discord_id=4714, playtime=338820),
    ]


#: The restriction views the read keeps rows by — the same closed vocabulary the page serves.
STATE_KEYS = {"banned": lambda row: bool(row["banned"]),
              "watched": lambda row: bool(row["watched"])}


class FakeRead:
    """The page's read seam: the directory's rows, filtered, paged and searched like the real action.

    The state filter and the closed range are reproduced on purpose — the read keeps only the state's
    rows and answers a page past the end with page 1, exactly as ``list_player_directory`` documents —
    because the page is what has to render the filtered set and turn a past-the-end number into the
    last page instead of an empty table.
    """

    def __init__(self, rows, per_page=50):
        self.rows = list(rows)
        self.per_page = per_page
        self.calls: list[dict] = []

    async def __call__(self, qualname, ctx, /, **params):
        assert qualname == "list_player_directory", qualname
        self.calls.append(dict(params))
        query = str(params.get("q") or "")
        state = str(params.get("state") or "all")
        keep = STATE_KEYS.get(state)
        matched = [row for row in self.rows
                   if (keep is None or keep(row))
                   and (not query or query.casefold() in row["names"][0].casefold()
                        or any(query.casefold() in ucid.casefold() for ucid in row["ucids"]))]
        total = len(matched)
        pages = max((total + self.per_page - 1) // self.per_page, 1)
        wanted = int(params.get("page") or 1)
        page = wanted if 1 <= wanted <= pages else 1
        start = (page - 1) * self.per_page
        return types.SimpleNamespace(success=True, data={
            "rows": matched[start:start + self.per_page], "total": total, "page": page,
            "pages": pages, "per_page": self.per_page, "query": query, "state": state})


def many_rows(count, **state):
    """*count* directory rows, every one carrying *state* (so a restriction view still pages)."""
    return [_entry(f"Player {index:03d}", f"{index:032x}", **state) for index in range(count)]


@pytest.fixture
def read(monkeypatch):
    """Install a stub directory read and hand the test the double."""
    fake = FakeRead(directory_rows())

    async def _read(qualname, ctx, /, **params):
        return await fake(qualname, ctx, **params)

    monkeypatch.setattr(players_page, "read_action", _read)
    return fake


@pytest.fixture
def app(config_dir):
    return build(config_dir, message_fleet())


async def open_page(http, query=""):
    response = await http.get(players_page.PLAYERS_PATH + query)
    assert response.status_code == 200, response.text
    return response.text


def row_html(html: str, name: str) -> str:
    """The rendered ``<tr>`` of the row whose name link reads *name*."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if f">{name}</a>" in row:
            return row
    raise AssertionError(f"no row rendered for {name!r}")


# --------------------------------------------------------------- 1. the route is registered, gated

@pytest.mark.asyncio
async def test_the_page_answers_200_for_a_role_holding_players_view(config_dir):
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(players_page.PLAYERS_PATH)

    assert response.status_code == 200, response.text
    assert "<h1>Players</h1>" in response.text


@pytest.mark.asyncio
async def test_the_page_answers_403_for_a_role_without_players_view(config_dir):
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        response = await http.get(players_page.PLAYERS_PATH)

    assert response.status_code == 403, response.text


# ------------------------------------------------------------- 2. the switch picks the SOURCE

@pytest.mark.asyncio
async def test_show_online_renders_the_live_roster_and_show_all_the_directory(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        online = await open_page(http, "?show=online")
        directory = await open_page(http, "?show=all")

    # the roster row exists only in the in-memory servers, the directory row only in the read
    assert "Fragmagnet" in online and UCID_A in online
    assert "Dirk" not in online
    assert "Dirk" in directory and DIRK in directory
    assert "Fragmagnet" not in directory
    # the roster keeps the dashboard's own table (its columns), the directory its own
    assert "Sorted by Player, ascending" in online and "Sorted by Player, ascending" in directory
    assert read.calls, "the directory view must ask the read; the roster view must not"


@pytest.mark.asyncio
async def test_the_roster_view_does_not_read_the_database(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        await open_page(http, "?show=online")

    assert read.calls == []


# ------------------------------------------------------------------ 3. each view keeps its rows

@pytest.mark.asyncio
async def test_show_banned_excludes_a_watched_only_row(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned")

    assert "Redeye" in html and "Thunder_84" in html
    assert "Mosquito-7" not in html, "a watched-only player must not appear in the ban view"
    assert "Dirk" not in html


@pytest.mark.asyncio
async def test_show_watched_excludes_a_banned_only_row(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=watched")

    assert "Redeye" in html and "Mosquito-7" in html
    assert "Thunder_84" not in html, "a banned-only player must not appear in the watch view"
    assert "Dirk" not in html


@pytest.mark.asyncio
async def test_a_view_name_the_page_does_not_know_falls_back_to_the_directory(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=sideways")

    assert "Dirk" in html, "an unknown view must render the directory"
    assert 'href="/players" aria-current="true">All</a>' in html, \
        "the All link must be the one marked as in force"


# -------------------------- 3b. a restriction view is filtered before the page is cut


def install_read(monkeypatch, fake):
    """Install *fake* as the page's directory read and hand it back."""

    async def _read(qualname, ctx, /, **params):
        return await fake(qualname, ctx, **params)

    monkeypatch.setattr(players_page, "read_action", _read)
    return fake


def banned_on_the_first_and_last_page(count=120):
    """*count* rows, two of them banned: one on the directory's FIRST page, one on its last."""
    rows = many_rows(count)
    rows[0]["banned"] = True
    rows[-1]["banned"] = True
    return rows


@pytest.mark.asyncio
async def test_the_page_asks_the_read_for_the_view_and_renders_what_it_served(
        monkeypatch, config_dir):
    """The state is the READ's: the page never filters the rows it was handed."""
    fake = install_read(monkeypatch, FakeRead(directory_rows()))
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        await open_page(http, "?show=banned")
        await open_page(http, "?show=watched")
        await open_page(http, "?show=all")

    assert [call["state"] for call in fake.calls] == ["banned", "watched", "all"], \
        "each directory view travels to the read as the state in force"


@pytest.mark.asyncio
async def test_a_banned_player_on_the_first_page_of_the_directory_is_on_the_ban_views_first_page(
        monkeypatch, config_dir):
    """Page 1 of the unfiltered set is not page 1 of the view: the read filters, the page renders."""
    install_read(monkeypatch, FakeRead(banned_on_the_first_and_last_page()))
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned")

    assert "Player 000" in html, "a banned player on the directory's first page must be in the view"
    assert "Player 119" in html, "…and so must one from the directory's last page"
    assert "Player 001" not in html, "a clean player must not appear in the ban view"


@pytest.mark.asyncio
async def test_a_views_total_and_pager_describe_the_filtered_set(monkeypatch, config_dir):
    """``2 players known`` and ONE page — the numbers, never the directory's 120."""
    install_read(monkeypatch, FakeRead(banned_on_the_first_and_last_page()))
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned")

    assert "2 players known" in html, "the count sentence states the FILTERED total"
    assert "120 players known" not in html
    assert "Page 2" not in html and "Next" not in html, "two rows are one page, not three"


@pytest.mark.asyncio
async def test_a_page_past_the_filtered_range_renders_the_empty_state(
        monkeypatch, config_dir):
    """No watched player at all: page 2 of the watch view is the empty state, not the directory."""
    install_read(monkeypatch, FakeRead(many_rows(120)))
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=watched&page=2")

    assert players_page.PLAYERS_EMPTY_VIEW in html, "an empty view states that it is empty"
    assert "Player 000" not in html, "the unfiltered directory must never fill a restriction view"
    assert 'search-state">0 players known' in html, \
        "the count sentence states the VIEW's total, not the directory's"


@pytest.mark.asyncio
async def test_the_search_and_the_state_compose(monkeypatch, config_dir):
    """``?show=banned&q=`` applies BOTH: the search narrows the view, the view narrows the search."""
    install_read(monkeypatch, FakeRead(banned_on_the_first_and_last_page()))
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned&q=Player 11")

    # the search names ten players, ONE of them banned: the view keeps that one, and says so
    assert "Player 119" in html, "the banned player the search names stays in the answer"
    assert "Player 110" not in html, "a clean player the search names is not in the ban view"
    assert "Player 000" not in html, "a banned player the search does not name is filtered out"
    assert "contains Player 11 · 1 players" in html, \
        "the count describes the searched AND filtered set, not the search alone"


# --------------------------------------------------- 4. a page past the end lands on the last page

@pytest.mark.asyncio
async def test_a_page_past_the_end_renders_the_last_page_not_an_empty_table(monkeypatch, config_dir):
    fake = FakeRead(many_rows(120))

    async def _read(qualname, ctx, /, **params):
        return await fake(qualname, ctx, **params)

    monkeypatch.setattr(players_page, "read_action", _read)
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?page=999")

    assert "Player 100" in html, "the last page's first row must be rendered"
    assert "Player 000" not in html, "the first page must not be rendered for a past-the-end number"
    assert "Page 3 of 3" in html


# ------------------------------------------------- 5. the switch and the search carry each other

@pytest.mark.asyncio
async def test_the_switch_links_carry_the_search_and_the_form_carries_the_view(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned&q=re")

    assert 'href="/players?q=re"' in html, "the All link must keep the search"
    assert re.search(r'href="/players\?q=re&amp;show=watched"', html), \
        "every view link must keep the search"
    assert '<input type="hidden" name="show" value="banned">' in html, \
        "a search typed inside a view must stay inside it"
    assert 'value="re"' in html, "the search box must echo the query"


@pytest.mark.asyncio
async def test_the_pager_links_carry_the_view_and_the_search(app, read):
    read.rows = many_rows(120, watched=True)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=watched&q=player")

    assert "show=watched" in html and "q=player" in html
    assert "Page 1 of 3" in html
    next_link = re.search(r'<a href="(/players\?[^"]*page=2[^"]*)">Next</a>', html)
    assert next_link, "the pager must offer the next page as a plain link"
    assert "show=watched" in next_link.group(1) and "q=player" in next_link.group(1)


# ----------------------------------------------------- 6. the state cell draws every restriction

@pytest.mark.asyncio
async def test_the_state_cell_prints_both_chips_ban_first(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    banned = '<span class="tag a">Banned</span>'
    watched = '<span class="tag a">Watched</span>'
    cell = row_html(html, "Redeye")
    assert banned in cell and watched in cell, "a banned AND watched player wears both chips"
    assert cell.index(banned) < cell.index(watched), "the ban chip comes first"
    assert banned in row_html(html, "Thunder_84") and watched not in row_html(html, "Thunder_84")
    assert watched in row_html(html, "Mosquito-7") and banned not in row_html(html, "Mosquito-7")


@pytest.mark.asyncio
async def test_a_clean_player_renders_a_dash_in_the_state_cell(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    cell = row_html(html, "Kolya")
    assert "Banned" not in cell and "Watched" not in cell and "VIP" not in cell
    assert "<td>—</td>" in cell, "a player with no restriction says so with a dash"


@pytest.mark.asyncio
async def test_a_vip_player_wears_the_vip_tag(app, read):
    read.rows = [_entry("Dirk", DIRK, vip=True)]
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert '<span class="tag">VIP</span>' in row_html(html, "Dirk")


# ---------------------------------------------------- 7. the status cell: who is flying

@pytest.mark.asyncio
async def test_an_online_row_renders_the_run_dot_and_the_word(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    cell = row_html(html, "Dirk")
    assert '<span class="dot run" aria-hidden="true"></span> <span class="tag">Online</span>' in cell, \
        "a player in a slot wears the run dot AND the word (a colour is never the only signal)"


@pytest.mark.asyncio
async def test_an_offline_row_renders_the_grey_stop_dot_and_the_word(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    cell = row_html(html, "Kolya")
    assert '<span class="dot stop" aria-hidden="true"></span> <span class="tag">Offline</span>' in cell
    assert "dot dead" not in cell, "a player who is not in a slot is the normal case, never a fault"


@pytest.mark.asyncio
async def test_status_is_the_first_column_and_sorts_online_first_with_the_name_tie_break(
        config_dir, monkeypatch):
    """Two online rows in reverse order and two offline ones: desc puts the flyers first by name."""
    rows = [_entry("Beta", "9e77c011-0000-4000-8000-000000000002"),
            _entry("Delta", "00000000000000000000000000000004"),
            _entry("Alpha", UCID_A),
            _entry("Charlie", "00000000000000000000000000000003")]

    async def _read(qualname, ctx, /, **params):
        return types.SimpleNamespace(success=True, data={
            "rows": rows, "total": len(rows), "page": 1, "pages": 1, "per_page": 50,
            "query": ""})

    monkeypatch.setattr(players_page, "read_action", _read)
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")          # unscoped: both roster UCIDs are online
        html = await open_page(http, "?sort=online&dir=desc")

    assert html.index('>Status <span class="ar"') < html.index('>Player <span class="ar"'), \
        "Status leads the row"
    order = [name for name in ("Alpha", "Beta", "Charlie", "Delta")
             if f'href="/players/' in html and name in html]
    positions = sorted(order, key=lambda name: html.index(f">{name}</a>"))
    assert positions == ["Alpha", "Beta", "Charlie", "Delta"], \
        "online rows first, each block in name order (the shared layer's tie-break)"


# --------------------------------- 8. no UCID count: the UCID is the player's key

@pytest.mark.asyncio
async def test_no_row_carries_a_ucid_count(app, read):
    read.rows = [_entry("Dirk", DIRK, ucids=(DIRK, "d1e5c0de0000000000000000000000bb"))]
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    cell = row_html(html, "Dirk")
    assert "UCID" not in cell, "the row carries no UCID meta line"
    assert DIRK in cell, "the UCID column still states the player's key"


# ------------------------------------------- 9. the moderation strip on the row

@pytest.fixture
def built(monkeypatch):
    """The control maps the page actually built, captured as they are handed to the table."""
    seen: dict[str, dict] = {}
    original = actions_page.controls_for

    def record(request, table, origin, **kw):
        value = original(request, table, origin, **kw)
        seen[table] = value
        return value

    monkeypatch.setattr(actions_page, "controls_for", record)
    return seen


def control_icons(strip) -> list[str]:
    """The glyph names of one row's built strip, in order."""
    return [control["icon"] for control in strip["strip"]]


def control_paths(cell: str) -> set[str]:
    """Every path the row's forms post to — the rendered half of the built record."""
    return set(re.findall(r'<form method="post" action="([^"]+)"', cell))


@pytest.mark.asyncio
async def test_online_and_offline_rows_both_offer_ban_and_watch(app, read, built):
    """The offline half is the point: neither control needs a live session."""
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    controls = built[players_page.PLAYERS_TABLE]
    assert control_icons(controls[DIRK]) == ["ban", "reveal"], "an ONLINE clean row: Ban + Watch"
    assert control_icons(controls[CLEAN]) == ["ban", "reveal"], "an OFFLINE clean row: the same pair"

    for name in ("Dirk", "Kolya"):
        cell = row_html(html, name)
        assert control_paths(cell) == {actions_page.BAN_ADD_ACTION.path + actions_page.CONFIRM_SUFFIX,
                                       actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX}, \
            f"{name} must render the two glyph forms"


@pytest.mark.asyncio
async def test_a_banned_row_offers_lift_ban_and_no_ban_glyph(app, read, built):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=banned")

    controls = built[players_page.PLAYERS_TABLE]
    assert control_icons(controls[GHOST]) == ["unban", "reveal"], "a banned row: Lift ban + Watch"
    cell = row_html(html, "Thunder_84")
    assert actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX in control_paths(cell)
    assert actions_page.BAN_ADD_PATH + actions_page.CONFIRM_SUFFIX not in cell, \
        "a banned row offers no Ban glyph"


@pytest.mark.asyncio
async def test_a_banned_and_watched_row_offers_lift_ban_and_unwatch(app, read, built):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert control_icons(built[players_page.PLAYERS_TABLE][REDEYE]) == ["unban", "conceal"], \
        "a banned AND watched row: Lift ban + Unwatch, in that order"
    cell = row_html(html, "Redeye")
    assert actions_page.WATCH_REMOVE_PATH + actions_page.CONFIRM_SUFFIX in control_paths(cell)
    assert actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX not in cell


@pytest.mark.asyncio
async def test_a_clean_row_offers_ban_and_watch_and_neither_release_glyph(app, read, built):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert control_icons(built[players_page.PLAYERS_TABLE][CLEAN]) == ["ban", "reveal"]
    cell = row_html(html, "Kolya")
    assert actions_page.BAN_LIFT_PATH not in cell and actions_page.WATCH_REMOVE_PATH not in cell


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?show=online", "?show=banned", "?show=watched"])
async def test_no_view_renders_a_live_session_control(app, read, query):
    """Message / Popup / Kick / Screenshot need a slot, so they are not row controls anywhere here."""
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, query)

    for path in (actions_page.CHAT_PATH, actions_page.POPUP_PATH,
                 "/actions/player/kick", "/actions/player/screenshot"):
        assert path not in html, f"a live-session control ({path}) must not be a row control"


@pytest.mark.asyncio
async def test_the_card_chip_reads_actions_on_the_directory(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert '<span class="tag">actions</span>' in html
    assert '<span class="tag">read-only</span>' not in html


@pytest.mark.asyncio
async def test_a_caller_without_players_watch_sees_no_watch_glyph(app, read, built, monkeypatch):
    original = actions_page.permissions.allows

    def deny_watch(capability, roles, *, manager=False):
        if capability == actions_page.WATCH_CAPABILITY:
            return False
        return original(capability, roles, manager=manager)

    monkeypatch.setattr(actions_page.permissions, "allows", deny_watch)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert control_icons(built[players_page.PLAYERS_TABLE][CLEAN]) == ["ban"], \
        "the watch control is ABSENT from the record, never rendered disabled"
    cell = row_html(html, "Kolya")
    assert actions_page.WATCH_ADD_PATH not in cell


@pytest.mark.asyncio
async def test_the_target_rides_the_body_not_the_action_url(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    cell = row_html(html, "Kolya")
    (form,) = forms_for(cell, actions_page.BAN_ADD_PATH + actions_page.CONFIRM_SUFFIX)
    assert f'<input type="hidden" name="ucid" value="{CLEAN}">' in form, \
        "the UCID travels in the BODY"
    assert f'name="_csrf_token"' in form, "every control carries the session's CSRF token"
    assert CLEAN not in form.split("</form>")[0].split('action="')[1].split('"')[0], \
        "no target in the action URL"


@pytest.mark.asyncio
async def test_a_crafted_post_for_a_player_outside_the_scope_is_the_bare_404(
        app, read, built, monkeypatch):
    """The dialog answers an out-of-scope UCID exactly as a UCID nothing answers to — nothing written."""
    async def _detail(qualname, ctx, /, **params):
        assert qualname == "get_player_detail", qualname
        return types.SimpleNamespace(success=True, data={"found": False})

    monkeypatch.setattr(actions_page, "read_action", _detail)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, "?show=all")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
        outside = await http.post(actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX,
                                  data={"ucid": CLEAN, "_csrf_token": token})
        missing = await http.post(actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX,
                                  data={"ucid": "0" * 32, "_csrf_token": token})
        after = await open_page(http, "?show=all")

    assert outside.status_code == missing.status_code == 404, outside.status_code
    assert outside.json()["detail"].startswith(missing.json()["detail"].split("'")[0]), \
        "an out-of-scope player answers the same sentence shape as one that does not exist"
    assert "Watched" not in row_html(after, "Kolya"), "the refusal wrote nothing"
    assert control_icons(built[players_page.PLAYERS_TABLE][CLEAN]) == ["ban", "reveal"], \
        "the row still offers Watch, so no entry was created"


# -------------------------------------------- 10. the name is the only door

@pytest.mark.asyncio
async def test_the_player_name_links_to_the_player_page(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert f'href="/players/{DIRK}"' in row_html(html, "Dirk"), \
        "the name is the door to the player's page, keyed by the UCID"
    assert f'href="{players_page.PLAYERS_PATH}/{REDEYE}"' in row_html(html, "Redeye")


# ------------------------------------------- 11. the write's outcome is stated, exactly once

def row_write_fields(cell_html: str, path: str) -> dict:
    """The hidden fields one rendered row form posts — read off the markup, never hand-built."""
    (form,) = forms_for(cell_html, path)
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


@pytest.mark.asyncio
async def test_a_row_write_states_its_outcome_on_the_page_it_lands_on(
        config_dir, read, clean_registry, store, watch_bus, audit_bot):
    """The row's own form writes, 303s back to this page, and the outcome is read once."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        listing = await open_page(http, "?show=all")
        fields = row_write_fields(row_html(listing, "Kolya"),
                                  actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX)
        assert fields.get(actions_page.ORIGIN_FIELD) == players_page.PLAYERS_PATH, \
            "the row states the page it returns to"
        reply = await http.post(actions_page.WATCH_ADD_PATH, data=dict(fields, reason="metagaming"))
        assert reply.status_code == 303, (reply.status_code, reply.text[:200])
        landed = await http.get(reply.headers["location"])
        again = await http.get(players_page.PLAYERS_PATH)

    assert CLEAN in store.watch, "the row's write landed"
    assert landed.status_code == 200, landed.text[:300]
    assert 'class="notice ok"' in landed.text, "the landing page states the write SUCCEEDED"
    assert CLEAN in notice(landed.text)
    assert notice(again.text) == "", "the notice is one-shot by design"


@pytest.mark.asyncio
async def test_a_write_naming_no_origin_still_lands_on_this_page_with_its_notice(
        config_dir, read, clean_registry, store, watch_bus, audit_bot):
    """A caller that sends no origin takes the section default, which is this page."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = await csrf(http)
        reply = await http.post(actions_page.WATCH_ADD_PATH,
                                data={"ucid": CLEAN, "reason": "metagaming",
                                      "_csrf_token": token})
        assert reply.status_code == 303, (reply.status_code, reply.text[:200])
        assert reply.headers["location"] == players_page.PLAYERS_PATH, reply.headers["location"]
        landed = await http.get(players_page.PLAYERS_PATH)

    assert CLEAN in store.watch, "the write landed"
    assert 'class="notice ok"' in landed.text, "the default return page states the outcome too"
    assert CLEAN in notice(landed.text)


# ----------------------------------------------------------------- 8. the page's own furniture

@pytest.mark.asyncio
async def test_the_directory_states_how_many_players_it_holds(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "")

    assert "5 players known" in html


@pytest.mark.asyncio
async def test_an_unlinked_player_says_not_linked(app, read):
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, "?show=all")

    assert "not linked" in row_html(html, "Mosquito-7")


@pytest.mark.asyncio
async def test_the_page_is_a_menu_entry_on_the_players_capability():
    """The nav item and the route read the SAME declaration (the capability's own name)."""
    (item,) = players_page.nav_items()

    assert item.url == players_page.PLAYERS_PATH
    assert item.label == players_page.PLAYERS_TITLE
    assert item.capability == players_page.PLAYERS_CAPABILITY
