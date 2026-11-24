"""The Players area's three reads: the directory, one player, one player's statistics.

The console's read shape, end to end where it can be driven without a database:

* the ACTION (``plugins/mission/actions.py``) is awaited with a context whose pool is a DOUBLE that
  answers each statement from a preset table and RECORDS the statement and its bound parameters, so
  "the search text is a parameter" and "page 2 asks for offset 50" are assertions about the bytes
  that reached the driver;
* the SHAPER (``services/webservice/readmodels/players.py``) is pure and is read off the action's
  own ``data``, so what the page will render is the same value these tests assert on.

Nothing here needs a live database, a Discord connection or an HTTP request: the two boundaries the
reads sit between are the pool and the view layer.
"""
from __future__ import annotations

import dataclasses
import datetime
import inspect
import types

import pytest

from core.actions import TRANSPORT_DISCORD, TRANSPORT_SERVICE, TRANSPORT_WEB, ActionContext
from plugins.mission import actions as mission_actions
from plugins.mission.actions import (PLAYER_DIRECTORY_PAGE_SIZE, PLAYERS_CONSOLE_ONLY,
                                     get_player_detail, get_player_statistics,
                                     list_player_directory)
from plugins.userstats.filter import PeriodFilter
from services.webservice import readmodels
from services.webservice.readmodels import views as view_layer
from services.webservice.readmodels.players import (directory_view, player_detail,
                                                    player_statistics, playtime_text)

NOW = datetime.datetime(2026, 10, 30, 11, 47)
EARLIER = datetime.datetime(2024, 1, 2, 8, 0)
NEVER = datetime.datetime(9999, 12, 31)

#: Which statement is being answered, by a marker that only that statement carries.
_DIRECTORY = "count(*) OVER ()"
_PLAYER = "WHERE ucid = %(ucid)s"
_PLAYER_SCOPE = "AS present"
_TOTALS = "FROM statistics s\n WHERE s.player_ucid"
_WATCH = "FROM watchlist"
_KILL_TYPES = "SUM(s.kills_planes)"
_STATS_TOTALS = "GREATEST(s.deaths, s.crashes)"
_PER_MODULE = "s.slot AS label"
_PER_SERVER = "m.server_name AS label"
_PER_MAP = "m.mission_theatre AS label"
_RECENT = "ORDER BY s.hop_on DESC"


class _Cursor:
    """A cursor that answers ONE statement from the pool's table and records it."""

    def __init__(self, pool):
        self._pool = pool
        self._rows: list[dict] = []
        self._iterator = iter(())

    async def execute(self, statement, params=None):
        self._rows = self._pool.answer(statement, params)
        self._pool.statements.append((statement, params))
        self._iterator = iter(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


class _Conn:
    """A connection handing out recording cursors; usable as ``async with pool.connection()``."""

    def __init__(self, pool):
        self._pool = pool

    def cursor(self, *args, **kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Pool:
    """An ``apool`` double: per-statement answers, and every statement it was asked to run."""

    def __init__(self, answers=None):
        #: ``[(marker, rows)]`` — the first marker found in a statement answers it
        self.answers: list[tuple[str, object]] = list(answers or [])
        self.statements: list[tuple[str, dict]] = []

    def connection(self):
        return _Conn(self)

    def answer(self, statement: str, params) -> list[dict]:
        for marker, rows in self.answers:
            if marker in statement:
                if not callable(rows):
                    given = rows
                elif len(inspect.signature(rows).parameters) > 1:
                    # a statement-aware answer can assert on the SQL it was asked to run
                    given = rows(statement, params)
                else:
                    given = rows(params)
                return [dict(row) for row in given]
        return []

    def statements_matching(self, marker: str) -> list[tuple[str, dict]]:
        return [(statement, params) for statement, params in self.statements if marker in statement]


class Scope:
    """A caller scope double: ``unrestricted`` plus the ``allows(managed_by)`` predicate."""

    def __init__(self, *, unrestricted: bool = False, managed: tuple[str, ...] = ()):
        self.unrestricted = unrestricted
        self._managed = managed

    def allows(self, managed_by) -> bool:
        return any(value in self._managed for value in (managed_by or ()))


def fleet(*servers: tuple[str, tuple[str, ...]]) -> dict:
    """A ``{name: server}`` mapping whose servers carry the given ``managed_by`` values."""
    return {name: types.SimpleNamespace(name=name, locals={"managed_by": list(managed)})
            for name, managed in servers}


def context(pool: Pool | None = None, *, scope=None, servers=None, bus=None) -> ActionContext:
    """A WEB action context over *pool*, the way the console builds one."""
    ctx = ActionContext(node=types.SimpleNamespace(apool=pool),
                        bus=bus if bus is not None else types.SimpleNamespace(
                            servers=servers if servers is not None else {}))
    ctx._transport = TRANSPORT_WEB
    ctx._scope = scope
    return ctx


def directory_row(**overrides) -> dict:
    """One grouped directory record, as the read's statement returns it."""
    row = {"player_key": "42", "names": ["Viper-1"], "ucids": ["u-viper"],
           "discord_id": 42, "first_seen": EARLIER, "last_seen": NOW, "vip": False,
           "banned": False, "watched": False, "playtime": 657660, "state": "", "total": 1}
    row.update(overrides)
    return row


def player_row(**overrides) -> dict:
    """One ``players`` row, as the read's statement returns it."""
    row = {"ucid": "u-viper", "name": "Viper-1", "discord_id": -1, "manual": True,
           "first_seen": EARLIER, "last_seen": NOW, "vip": False}
    row.update(overrides)
    return row


def totals_row(**overrides) -> dict:
    row = {"playtime": 3600, "sessions": 3, "kills": 9, "pvp": 4, "deaths": 5,
           "ejections": 1, "crashes": 3, "takeoffs": 6, "landings": 5, "teamkills": 1,
           "deaths_ai": 2, "deaths_pvp": 2, "selfkills": 1}
    row.update(overrides)
    return row


# ═══════════════════════════════════════════════ 1. the directory: rows, totals, paging


@pytest.mark.asyncio
async def test_the_directory_answers_a_page_and_the_total_behind_it():
    """The read returns the page's rows AND the total, so the page can say "50 of 1,284"."""
    rows = [directory_row(player_key=str(index), names=[f"Player {index}"], ucids=[f"u-{index}"],
                          discord_id=index, total=1284) for index in range(50)]
    pool = Pool([(_DIRECTORY, rows)])
    result = await list_player_directory(context(pool), page="1")

    assert result.success is True
    assert result.data["total"] == 1284
    assert result.data["per_page"] == PLAYER_DIRECTORY_PAGE_SIZE
    assert result.data["pages"] == 26, "1,284 players at 50 a page are 26 pages"
    assert len(result.data["rows"]) == 50
    assert result.data["page"] == 1

    page = directory_view(result.data)
    assert page.shown == 50
    assert page.label == "50 of 1284 players"


@pytest.mark.asyncio
async def test_fifty_rows_a_page_lands_on_the_right_offset():
    """``?page=3`` asks the database for the third page, never for the first three."""
    rows = [directory_row() for _ in range(50)]
    pool = Pool([(_DIRECTORY, rows)])
    result = await list_player_directory(context(pool), page="3")

    assert result.data["page"] == 3
    (statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["limit"] == PLAYER_DIRECTORY_PAGE_SIZE
    assert params["offset"] == 2 * PLAYER_DIRECTORY_PAGE_SIZE, "page 3 starts at row 100"
    assert "LIMIT %(limit)s OFFSET %(offset)s" in statement, "the page is paged in SQL"


@pytest.mark.asyncio
async def test_an_out_of_range_page_is_served_page_one():
    """A page past the end answers the FIRST page and states the page it actually served."""
    rows = [directory_row(total=800)]
    pool = Pool()

    def answer(params):
        # the past-the-end page is empty; the first page holds everything
        return rows if params["offset"] == 0 else []

    pool.answers = [(_DIRECTORY, answer)]
    result = await list_player_directory(context(pool), page="9999")

    assert result.success is True
    assert result.data["page"] == 1, "the RESOLVED page is what the answer states"
    assert len(result.data["rows"]) == 1
    offsets = [params["offset"] for _statement, params in pool.statements_matching(_DIRECTORY)]
    assert offsets == [9998 * PLAYER_DIRECTORY_PAGE_SIZE, 0]


@pytest.mark.parametrize("value", ["", "abc", "-3", "0", "1.5", "  ", None])
@pytest.mark.asyncio
async def test_a_non_numeric_or_negative_page_is_page_one(value):
    """A page number the URL made up is page 1, asserted as the RESOLVED value."""
    pool = Pool([(_DIRECTORY, [directory_row(total=1)])])
    result = await list_player_directory(context(pool), page=value)

    assert result.data["page"] == 1
    (_statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["offset"] == 0


def test_the_page_validator_closes_the_range():
    """The ONE page validator: numbers inside the range pass, everything else is page 1."""
    assert view_layer.normalise_page("2", pages=3) == 2
    assert view_layer.normalise_page("3", pages=3) == 3
    assert view_layer.normalise_page("4", pages=3) == 1
    assert view_layer.normalise_page("-1", pages=3) == 1
    assert view_layer.normalise_page("x", pages=3) == 1
    assert view_layer.normalise_page(None, pages=3) == 1


# ═══════════════════════════════════════════════ 2. the search


@pytest.mark.asyncio
async def test_the_search_matches_a_name_or_a_ucid():
    """``?q=`` arrives as ONE bound needle, and the statement tests the name and the UCID with it."""
    pool = Pool([(_DIRECTORY, [directory_row()])])
    await list_player_directory(context(pool), q="  Viper  ")

    (statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["needle"] == "Viper", "the query is trimmed exactly as the view layer trims it"
    assert params["pattern"] == "%Viper%"
    assert "unnest(g.ucids)" in statement and "unnest(g.names)" in statement, \
        "the search must reach both a UCID and a DCS name"


@pytest.mark.asyncio
async def test_a_quote_in_the_search_travels_as_a_bound_parameter():
    """A quote is DATA: it appears in the parameters and never in the statement."""
    hostile = "O'Brien\"; DROP TABLE players; --"
    pool = Pool([(_DIRECTORY, [])])
    await list_player_directory(context(pool), q=hostile)

    (statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["needle"] == hostile
    assert "O'Brien" not in statement, "the search text was interpolated into the SQL"
    assert "DROP TABLE" not in statement
    assert statement.count("%(needle)s") == 1


@pytest.mark.asyncio
async def test_the_like_metacharacters_are_escaped_in_the_pattern():
    """``%`` and ``_`` in a search match those characters, not "anything"."""
    pool = Pool([(_DIRECTORY, [])])
    await list_player_directory(context(pool), q="100%_done")

    (_statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["pattern"] == "%100\\%\\_done%"
    assert params["needle"] == "100%_done", "the value itself is unchanged"


# ═══════════════════════════════════════════════ 3. scope


@pytest.mark.asyncio
async def test_an_admin_reads_every_player_and_a_scoped_holder_only_their_servers():
    """The scope is a PARAMETER of the read: ``None`` for an Admin, the caller's set otherwise."""
    admin_pool = Pool([(_DIRECTORY, [directory_row()])])
    admin = await list_player_directory(context(admin_pool, scope=Scope(unrestricted=True)))
    (_s, admin_params), = admin_pool.statements_matching(_DIRECTORY)
    assert admin_params["servers"] is None, "an unscoped caller reads the whole installation"
    assert admin.success is True

    scoped_pool = Pool([(_DIRECTORY, [directory_row()])])
    scoped_ctx = context(scoped_pool, scope=Scope(managed=("Host A",)),
                         servers=fleet(("SRS-1", ("Host A",)), ("SRS-B", ("Host B",))))
    scoped = await list_player_directory(scoped_ctx)
    (_s, scoped_params), = scoped_pool.statements_matching(_DIRECTORY)
    assert scoped_params["servers"] == ["SRS-1"], "a manager's read names only their own servers"
    assert scoped.success is True


@pytest.mark.asyncio
async def test_a_narrowed_callers_rows_exclude_a_player_of_a_foreign_server():
    """The double applies the SAME predicate the SQL does: a foreign player is not in the answer."""
    def answer(params):
        if params["servers"] is None:
            return [directory_row(player_key="1", names=["Mine"], ucids=["u-mine"], total=2),
                    directory_row(player_key="2", names=["Foreign"], ucids=["u-foreign"],
                                  discord_id=99, total=2)]
        return [directory_row(names=["Mine"], ucids=["u-mine"], total=1)]

    pool = Pool([(_DIRECTORY, answer)])
    scoped = directory_view((await list_player_directory(
        context(pool, scope=Scope(managed=("Host A",)),
                servers=fleet(("SRS-1", ("Host A",)), ("SRS-B", ("Host B",)))))).data)
    assert [row.name for row in scoped.rows] == ["Mine"]

    admin_pool = Pool([(_DIRECTORY, answer)])
    admin = directory_view((await list_player_directory(
        context(admin_pool, scope=Scope(unrestricted=True)))).data)
    assert [row.name for row in admin.rows] == ["Mine", "Foreign"]


@pytest.mark.asyncio
async def test_a_narrowed_caller_with_no_servers_reads_nobody():
    """An empty set is NOT the global read: the two spellings are different parameters."""
    pool = Pool([(_DIRECTORY, [])])
    result = await list_player_directory(context(pool, scope=Scope(managed=()),
                                                servers=fleet(("SRS-B", ("Host B",)))))
    (_statement, params), = pool.statements_matching(_DIRECTORY)
    assert params["servers"] == [], "a narrowed caller with no servers must not read globally"
    assert result.data["total"] == 0


# ══════════════════════════════════ 3b. a state view filters before the page is cut


#: The predicate each directory state view adds to the statement — the allow-list the read keeps.
STATE_PREDICATES: dict[str, str] = {"all": "", "banned": "AND g.banned", "watched": "AND g.watched"}


def state_answer(rows):
    """Answer a directory page from *rows*, applying the state predicate the STATEMENT carries.

    The double mirrors the read's own contract: the view keeps only its rows, the search narrows
    them, and only then is the page cut — so the rows and the ``total`` it reports describe the
    filtered set, exactly as the SQL's WHERE-before-LIMIT does.
    """
    def answer(statement: str, params):
        state = next((key for key, predicate in STATE_PREDICATES.items()
                      if predicate and predicate in statement), "all")
        kept = [row for row in rows if state == "all" or row[state]]
        query = str(params.get("needle") or "").casefold()
        matched = [row for row in kept
                   if not query or query in row["names"][0].casefold()
                   or any(query in ucid.casefold() for ucid in row["ucids"])]
        offset, limit = int(params["offset"]), int(params["limit"])
        return [dict(row, total=len(matched)) for row in matched[offset:offset + limit]]
    return answer


def directory_across_pages(count=120, **banned_on):
    """*count* directory rows in name order, the given indices carrying the row's own ``banned``."""
    rows = [directory_row(player_key=str(index), names=[f"Player {index:03d}"],
                          ucids=[f"u-{index:03d}"], discord_id=index) for index in range(count)]
    for index in banned_on.get("banned", ()):
        rows[index]["banned"] = True
    return rows


@pytest.mark.asyncio
async def test_a_state_view_is_a_predicate_before_the_limit():
    """The Banned view keeps its rows INSIDE the statement, before the page is cut."""
    pool = Pool([(_DIRECTORY, state_answer([directory_row(banned=True)]))])
    result = await list_player_directory(context(pool), state="banned")

    (statement, _params), = pool.statements_matching(_DIRECTORY)
    assert "AND g.banned" in statement, "the view must be part of the statement"
    assert statement.index("AND g.banned") < statement.index("LIMIT %(limit)s OFFSET %(offset)s"), \
        "the filter must exist before the LIMIT, never after it"
    assert result.data["state"] == "banned", "the answer states the view it served"
    assert "unnest(g.names)" in statement, "the search is still one statement, not a second query"


@pytest.mark.asyncio
async def test_a_banned_player_on_the_first_page_of_the_directory_is_on_the_views_first_page():
    """The reviewer's case: 120 players, one banned on page 1, one on page 3."""
    pool = Pool([(_DIRECTORY, state_answer(directory_across_pages(banned=(0, 119))))])
    result = await list_player_directory(context(pool), state="banned", page="1")

    assert [row["names"][0] for row in result.data["rows"]] == ["Player 000", "Player 119"], \
        "both banned players are on the view's first page, whatever page they sit on"


@pytest.mark.asyncio
async def test_a_state_views_total_and_pages_describe_the_filtered_set():
    """``2 players`` and ONE page — never the directory's 120."""
    pool = Pool([(_DIRECTORY, state_answer(directory_across_pages(banned=(0, 119))))])
    result = await list_player_directory(context(pool), state="banned", page="1")

    assert result.data["total"] == 2
    assert result.data["pages"] == 1, "two rows are one page, not three"
    assert result.data["page"] == 1
    assert len(result.data["rows"]) == 2


@pytest.mark.asyncio
async def test_a_page_past_the_filtered_range_is_answered_with_the_views_last_page():
    """The closed range is the FILTERED range: page 2 of a two-row view is its first page."""
    pool = Pool([(_DIRECTORY, state_answer(directory_across_pages(banned=(0, 119))))])
    result = await list_player_directory(context(pool), state="banned", page="2")

    assert result.data["page"] == 1, "a past-the-end page of the view falls back to its last page"
    assert len(result.data["rows"]) == 2

    empty = Pool([(_DIRECTORY, state_answer(directory_across_pages()))])
    nothing = await list_player_directory(context(empty), state="watched")
    assert nothing.data["total"] == 0 and nothing.data["rows"] == []
    assert nothing.data["pages"] == 1, "an empty view is one empty page, never an error"


@pytest.mark.asyncio
async def test_the_search_and_the_state_compose_in_the_same_statement():
    """Neither wins: the needle and the view are two terms of ONE statement."""
    pool = Pool([(_DIRECTORY, state_answer(directory_across_pages(banned=(0, 119))))])
    result = await list_player_directory(context(pool), state="banned", q="119")

    (statement, params), = pool.statements_matching(_DIRECTORY)
    assert "AND g.banned" in statement and "unnest(g.names)" in statement
    assert params["needle"] == "119"
    assert [row["names"][0] for row in result.data["rows"]] == ["Player 119"]
    assert result.data["total"] == 1, "the total describes the searched AND filtered set"


@pytest.mark.parametrize("value", ["../../etc", "banned; DROP TABLE players; --", "sideways",
                                   "online", "", None, "   "])
@pytest.mark.asyncio
async def test_a_state_key_outside_the_allow_list_is_the_whole_directory(value):
    """A crafted ``?show=`` is not an error and not a predicate: it is the directory."""
    crafted = Pool([(_DIRECTORY, [])])
    await list_player_directory(context(crafted), state=value)
    (statement, _params), = crafted.statements_matching(_DIRECTORY)

    plain = Pool([(_DIRECTORY, [])])
    await list_player_directory(context(plain))
    (expected, _plain_params), = plain.statements_matching(_DIRECTORY)

    assert statement == expected, "a value outside the allow-list must not change the statement"
    assert "AND g." not in statement
    if str(value or "").strip():
        assert str(value).strip() not in statement, "the request text reached the SQL"


@pytest.mark.asyncio
async def test_the_allow_list_is_the_three_states_the_page_serves():
    """One spelling: the read's keys are the page's view names, and nothing else."""
    assert set(STATE_PREDICATES) == set(mission_actions._DIRECTORY_STATES)
    assert set(mission_actions._DIRECTORY_STATES) == {"all", "banned", "watched"}
    for state, predicate in mission_actions._DIRECTORY_STATES.items():
        assert STATE_PREDICATES[state] == predicate.strip()


@pytest.mark.parametrize("state", ["all", "banned", "watched"])
@pytest.mark.asyncio
async def test_a_scoped_callers_rows_stay_scoped_in_every_state_view(state):
    """The scope is a term of the same statement, so a restriction view narrows nothing away."""
    marked = {} if state == "all" else {state: True}
    rows = [directory_row(player_key="1", names=["Mine"], ucids=["u-mine"], total=2, **marked),
            directory_row(player_key="2", names=["Foreign"], ucids=["u-foreign"], discord_id=99,
                          total=2, **marked)]
    scoped_pool = Pool([(_DIRECTORY, state_answer(rows[:1]))])
    scoped = await list_player_directory(
        context(scoped_pool, scope=Scope(managed=("Host A",)),
                servers=fleet(("SRS-1", ("Host A",)), ("SRS-B", ("Host B",)))), state=state)
    (_s, scoped_params), = scoped_pool.statements_matching(_DIRECTORY)
    assert scoped_params["servers"] == ["SRS-1"], "the scope is still bound, in every view"
    assert [row["names"][0] for row in scoped.data["rows"]] == ["Mine"]

    admin_pool = Pool([(_DIRECTORY, state_answer(rows))])
    admin = await list_player_directory(context(admin_pool, scope=Scope(unrestricted=True)),
                                        state=state)
    assert [row["names"][0] for row in admin.data["rows"]] == ["Mine", "Foreign"]


# ═══════════════════════════════════════════════ 4. one row per player


@pytest.mark.asyncio
async def test_two_ucids_of_one_player_are_one_row_carrying_both():
    """The grouping is the read's: a link with two UCIDs is ONE row, never two."""
    pool = Pool([(_DIRECTORY, [directory_row(names=["Viper-1", "viper-old"],
                                             ucids=["u-viper", "u-viper-old"],
                                             discord_id=42, total=1)])])
    page = directory_view((await list_player_directory(context(pool))).data)

    assert len(page.rows) == 1
    row = page.rows[0]
    assert row.ucids == ("u-viper", "u-viper-old")
    assert row.ucid_count == 2
    assert row.ucid == "u-viper", "the row's own key is its first UCID"
    assert row.name == "Viper-1", "the name shown is the most recently used one"
    assert row.discord == "42"


@pytest.mark.asyncio
async def test_the_rows_carry_the_moderation_state():
    """Ban, watch and VIP are per-row facts; the state token states them in a fixed order."""
    pool = Pool([(_DIRECTORY, [
        directory_row(player_key="1", ucids=["u-b"], banned=True, watched=True, total=2),
        directory_row(player_key="2", names=["Clean"], ucids=["u-c"], discord_id=7, total=2)])])
    rows = directory_view((await list_player_directory(context(pool))).data).rows

    assert rows[0].banned and rows[0].watched and not rows[0].vip
    assert rows[0].state == "banned watched", "ban before watch, always"
    assert not rows[1].banned and not rows[1].watched
    assert rows[1].state == ""


def test_a_player_with_no_statistics_renders_no_playtime():
    """A NULL duration renders NOTHING — never ``0 h``, which would state a measurement."""
    row = directory_view({"rows": [directory_row(playtime=None)], "total": 1}).rows[0]
    assert row.playtime == ""
    assert row.playtime_seconds is None
    assert playtime_text(None) == ""
    assert playtime_text(0) == "0 s", "a MEASURED zero is a value; an absent one is not"
    assert playtime_text(657660) == "182 h 41 m"


def test_a_null_name_renders_nothing_and_a_placeholder_is_never_invented():
    """A NULL DCS name is an empty cell — not the UCID, not ``n/a``."""
    row = directory_view({"rows": [directory_row(names=[], ucids=["u-anon"], total=1)],
                          "total": 1}).rows[0]
    assert row.name == ""
    assert row.ucid == "u-anon"


@pytest.mark.asyncio
async def test_the_directory_never_asks_discord_for_a_member_name(monkeypatch):
    """The directory is a database read: it resolves no members, so no render costs API calls."""
    from services.webservice.readmodels import serverconfig

    class Bot:
        async def get_members(self, user_ids, fetch=True):
            raise AssertionError("the directory must not resolve members")

    monkeypatch.setattr(serverconfig, "live_bot", lambda: Bot())
    pool = Pool([(_DIRECTORY, [directory_row()])])
    row = directory_view((await list_player_directory(context(pool))).data).rows[0]

    assert row.discord == "42", "the row carries the id, never a name"
    assert not hasattr(row, "member")


@pytest.mark.asyncio
async def test_the_player_page_resolves_the_member_name_from_discord(monkeypatch):
    """ONE player: the shown name is fetched when uncached, and it is the display name."""
    from services.webservice.readmodels import serverconfig

    class Member:
        name = "dcs_specialk"
        display_name = "Special K"

    class Bot:
        async def get_members(self, user_ids, fetch=True):
            assert fetch is True, "the player page may look a member up"
            return {int(list(user_ids)[0]): Member()}

    monkeypatch.setattr(serverconfig, "live_bot", lambda: Bot())
    pool = Pool([(_PLAYER, [player_row(discord_id=42)]), (_WATCH, [])])
    view = player_detail((await get_player_detail(context(pool), ucid="u-viper")).data)

    assert view.member == "Special K", "nickname or global name, never the username"


@pytest.mark.asyncio
async def test_the_player_page_renders_without_a_discord_connection(monkeypatch):
    """No bot process: the page still answers, with no name and the id intact."""
    from services.webservice.readmodels import serverconfig

    monkeypatch.setattr(serverconfig, "live_bot", lambda: None)
    pool = Pool([(_PLAYER, [player_row(discord_id=42)]), (_WATCH, [])])
    view = player_detail((await get_player_detail(context(pool), ucid="u-viper")).data)

    assert view.found is True
    assert view.member == ""
    assert view.discord_id == "42"


# ═══════════════════════════════════════════════ 5. one player


@pytest.mark.asyncio
async def test_a_clean_player_answers_every_moderation_key_empty():
    """A player who is neither banned nor watched has the same KEYS, with empty values."""
    pool = Pool([(_PLAYER, [player_row()]), (_WATCH, [])])
    result = await get_player_detail(context(pool), ucid="u-viper")

    assert result.success is True
    assert result.data["found"] is True
    assert result.data["ban"] is None and result.data["watch"] is None

    view = player_detail(result.data)
    keys = {field.name for field in dataclasses.fields(view)}
    assert {"ban", "watch", "playtime", "sessions", "identity", "member"} <= keys
    ban_keys = {field.name for field in dataclasses.fields(view.ban)}
    assert {"active", "reason", "banned_by", "banned_until", "permanent", "scope"} <= ban_keys
    watch_keys = {field.name for field in dataclasses.fields(view.watch)}
    assert {"active", "reason", "created_by", "created_at"} <= watch_keys

    assert view.ban.active is False
    assert (view.ban.reason, view.ban.banned_by, view.ban.banned_until, view.ban.scope) == \
        ("", "", "", "")
    assert view.watch.active is False
    assert (view.watch.reason, view.watch.created_by, view.watch.created_at) == ("", "", "")


@pytest.mark.asyncio
async def test_a_player_the_read_never_saw_answers_the_same_shape():
    """An unknown UCID is ``found`` False — never a missing key and never a raised error."""
    pool = Pool([(_PLAYER, [])])
    view = player_detail((await get_player_detail(context(pool), ucid="u-ghost")).data)

    assert view.found is False
    assert view.ban.active is False and view.watch.active is False
    assert view.identity == ()


@pytest.mark.asyncio
async def test_a_permanent_ban_renders_the_shared_sentinel_word():
    """``banned_until`` in the year 9999 reads ``never`` through ``readmodels.bans.permanent``."""
    class Bus:
        servers: dict = {}

        async def ban(self, *args, **kwargs):
            raise AssertionError("a read must not write a ban")

        async def bans(self, *, expired=False, servers=None):
            return [{"ucid": "u-viper", "name": "Viper-1", "banned_by": "mod",
                     "reason": "cheating", "banned_at": NOW, "banned_until": NEVER,
                     "server_name": ["SRS-1", "SRS-B"]}]

    pool = Pool([(_PLAYER, [player_row()]), (_WATCH, [])])
    result = await get_player_detail(context(pool, bus=Bus()), ucid="u-viper")
    view = player_detail(result.data)

    assert view.ban.active is True
    assert view.ban.permanent is True
    assert view.ban.banned_until == "never", "the sentinel is rendered as the shared word"
    assert "9999" not in view.ban.banned_until
    assert view.ban.scope == "SRS-1, SRS-B", "a scoped ban names its servers"
    assert view.ban.banned_at == "2026-10-30 11:47 UTC"


@pytest.mark.asyncio
async def test_a_bot_wide_ban_states_every_server():
    """``server_name IS NULL`` is the bus's spelling of a bot-wide entry, and reads as such."""
    class Bus:
        servers: dict = {}

        async def ban(self, *args, **kwargs):
            raise AssertionError("a read must not write a ban")

        async def bans(self, *, expired=False, servers=None):
            return [{"ucid": "u-viper", "banned_by": "cloud", "reason": "DGSA: cheating",
                     "banned_at": NOW, "banned_until": NOW + datetime.timedelta(days=7),
                     "server_name": None}]

    pool = Pool([(_PLAYER, [player_row()]), (_WATCH, [])])
    view = player_detail((await get_player_detail(context(pool, bus=Bus()),
                                                  ucid="u-viper")).data)
    assert view.ban.scope == readmodels.ALL_SERVERS
    assert view.ban.permanent is False
    assert view.ban.servers == ()


@pytest.mark.asyncio
async def test_a_watched_player_carries_the_watch_entry():
    """The watch entry is the newest ``watchlist`` row of the link, with its author and date."""
    pool = Pool([(_PLAYER, [player_row()]),
                 (_WATCH, [{"ucid": "u-viper", "reason": "aimbot review",
                            "created_by": "mod", "created_at": EARLIER}])])
    view = player_detail((await get_player_detail(context(pool), ucid="u-viper")).data)

    assert view.watch.active is True
    assert view.watch.reason == "aimbot review"
    assert view.watch.created_by == "mod"
    assert view.watch.created_at == "2024-01-02 08:00 UTC"
    assert view.ban.active is False, "watching is not banning"


@pytest.mark.asyncio
async def test_a_player_outside_a_narrowed_callers_scope_is_not_found():
    """The detail read is scope-bound like the directory: a foreign player is not disclosed."""
    pool = Pool([(_PLAYER, [player_row()]), (_PLAYER_SCOPE, [{"present": False}])])
    ctx = context(pool, scope=Scope(managed=("Host A",)), servers=fleet(("SRS-1", ("Host A",))))
    result = await get_player_detail(ctx, ucid="u-viper")

    assert result.success is True
    assert result.data["found"] is False, "a player outside the scope answers as not found"
    assert result.data["name"] == ""
    assert pool.statements_matching(_WATCH) == [], "the watch of a foreign player was not read"

    allowed = Pool([(_PLAYER, [player_row()]), (_PLAYER_SCOPE, [{"present": True}]), (_WATCH, [])])
    ctx = context(allowed, scope=Scope(managed=("Host A",)), servers=fleet(("SRS-1", ("Host A",))))
    assert (await get_player_detail(ctx, ucid="u-viper")).data["found"] is True


@pytest.mark.asyncio
async def test_a_player_with_two_ucids_is_read_as_one_player():
    """The identity list carries every UCID of the link, and the totals are read over all of them."""
    pool = Pool([(_PLAYER, [player_row(discord_id=42)]),
                 ("WHERE discord_id = %(discord_id)s",
                  [{"ucid": "u-viper", "name": "Viper-1", "manual": True},
                   {"ucid": "u-old", "name": "viper-old", "manual": False}]),
                 (_TOTALS, [{"playtime": 7200, "sessions": 4}]),
                 (_WATCH, [])])
    view = player_detail((await get_player_detail(context(pool), ucid="u-viper")).data)

    assert [entry.ucid for entry in view.identity] == ["u-viper", "u-old"]
    assert view.identity[1].manual is False, "the manual flag is per UCID"
    assert view.playtime == "2 h 0 m"
    assert view.sessions == 4
    (_statement, params), = pool.statements_matching(_TOTALS)
    assert params["ucids"] == ["u-viper", "u-old"], "the totals cover the whole link"


# ═══════════════════════════════════════════════ 6. one player's statistics


def statistics_pool(**overrides) -> Pool:
    answers = {
        _PLAYER: [player_row()],
        _STATS_TOTALS: [totals_row()],
        _KILL_TYPES: [{"planes": 5, "helicopters": 1, "ships": 0, "sams": 2, "ground": 1}],
        _PER_MODULE: [{"label": "A-10C II", "playtime": 3600}],
        _PER_SERVER: [{"label": "SRS-1", "playtime": 1800}],
        _PER_MAP: [{"label": "Caucasus", "playtime": 3600}],
        _RECENT: [{"hop_on": NOW, "hop_off": NOW + datetime.timedelta(hours=1),
                   "slot": "A-10C II", "server_name": "SRS-1", "mission_theatre": "Caucasus",
                   "playtime": 3600}],
    }
    answers.update(overrides)
    return Pool(list(answers.items()))


@pytest.mark.asyncio
@pytest.mark.parametrize("period", PeriodFilter.list(None))
async def test_the_statistics_accept_every_period_name(period):
    """Every name the bot publishes is accepted, and the answer states the period it used."""
    pool = statistics_pool()
    result = await get_player_statistics(context(pool), ucid="u-viper", period=period)

    assert result.success is True
    assert result.data["period"] == period
    view = player_statistics(result.data)
    assert view.period == period


@pytest.mark.asyncio
async def test_the_period_is_the_bots_own_filter(monkeypatch):
    """The statement carries the fragment ``plugins/userstats/filter.py`` builds — not a copy."""
    from plugins.userstats.filter import StatisticsFilter

    seen: list[str] = []
    original = StatisticsFilter.detect

    def detect(bot, period):
        seen.append(period)
        return original(bot, period)

    monkeypatch.setattr(StatisticsFilter, "detect", staticmethod(detect))
    pool = statistics_pool()
    result = await get_player_statistics(context(pool), ucid="u-viper", period="quarter")

    assert seen == ["quarter"], "the console reads the bot's own definition of a period"
    (_statement, _params), = pool.statements_matching(_STATS_TOTALS)
    assert "interval '3 months'" in _statement, "quarter is the bot's own 3-month interval"
    assert result.data["period"] == "quarter"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["garbage", "", "yesterday-ish", "mission:foo", "week-"])
async def test_an_unknown_period_falls_back_to_all(value):
    """Anything outside the bot's vocabulary is ``all`` — never a second reading of a period."""
    pool = statistics_pool()
    result = await get_player_statistics(context(pool), ucid="u-viper", period=value)

    assert result.data["period"] == "all"
    (statement, _params), = pool.statements_matching(_STATS_TOTALS)
    assert "1 = 1" in statement, "the all period adds no time filter at all"


@pytest.mark.asyncio
async def test_the_statistics_carry_the_bots_own_figures():
    """Kills, the AI/player split, deaths by cause, flight figures and the last sessions."""
    pool = statistics_pool()
    view = player_statistics((await get_player_statistics(context(pool), ucid="u-viper")).data)

    assert view.playtime == "1 h 0 m"
    assert view.sessions == 3
    assert (view.kills, view.player_kills, view.ai_kills) == (9, 4, 5)
    assert (view.deaths_ai, view.deaths_player, view.selfkills) == (2, 2, 1)
    assert (view.takeoffs, view.landings, view.ejections) == (6, 5, 1)
    assert view.crashes == 2, "crashes is the pilot-dead count: crashes - ejections"
    assert view.teamkills == 1
    assert [entry.label for entry in view.kills_by_type] == \
        ["planes", "helicopters", "ships", "sams", "ground"]
    assert view.kills_by_type[0].count == 5
    assert [entry.label for entry in view.per_module] == ["A-10C II"]
    assert [entry.label for entry in view.per_server] == ["SRS-1"]
    assert [entry.label for entry in view.per_map] == ["Caucasus"]
    assert len(view.recent) == 1
    assert view.recent[0].started == "2026-10-30 11:47 UTC"
    assert view.recent[0].playtime == "1 h 0 m"


@pytest.mark.asyncio
async def test_the_recent_sessions_are_capped_and_ordered_newest_first():
    """The read asks for the last N sessions, newest first — the SQL does the ordering."""
    pool = statistics_pool()
    await get_player_statistics(context(pool), ucid="u-viper")
    (statement, params), = pool.statements_matching(_RECENT)

    assert params["limit"] == mission_actions.PLAYER_RECENT_SESSIONS
    assert "ORDER BY s.hop_on DESC" in statement


# ═══════════════════════════════════════════════ 7. the seams that stay shut


@pytest.mark.parametrize("transport", [TRANSPORT_DISCORD, TRANSPORT_SERVICE, ""])
@pytest.mark.asyncio
async def test_every_read_refuses_a_non_console_transport(transport):
    """A transport without a scope would read the WHOLE installation: all three refuse it."""
    pool = Pool([(_DIRECTORY, [directory_row()]), (_PLAYER, [player_row()])])
    ctx = context(pool)
    ctx._transport = transport

    for call in (list_player_directory(ctx), get_player_detail(ctx, ucid="u-viper"),
                 get_player_statistics(ctx, ucid="u-viper")):
        result = await call
        assert result.success is False
        assert result.message == PLAYERS_CONSOLE_ONLY
    assert pool.statements == [], "a refused read still reached the database"


@pytest.mark.asyncio
async def test_a_read_without_a_pool_is_a_typed_refusal():
    """No pool is an installation fact, not an AttributeError."""
    ctx = ActionContext(node=types.SimpleNamespace(), bus=types.SimpleNamespace(servers={}))
    ctx._transport = TRANSPORT_WEB
    result = await list_player_directory(ctx)
    assert result.success is False
    assert result.message == mission_actions.PLAYERS_UNAVAILABLE


def test_the_page_size_and_the_sort_keys_have_one_home():
    """The read's ORDER BY and the console's table spec cannot drift apart.

    The ONE exception is ``online``: the live marker is overlaid by the page from the in-memory
    roster, because the database cannot answer who holds a slot. The read therefore cannot order by
    it, and the page's own sort layer does — so the pin is the table's keys minus exactly that one,
    and the live column must exist for the page to sort it.
    """
    assert PLAYER_DIRECTORY_PAGE_SIZE == view_layer.PLAYER_DIRECTORY_PAGE_SIZE
    keys = set(view_layer.column_keys("player_directory"))
    assert "online" in keys, "the status column is the page's own, but it must be declared"
    assert set(mission_actions._DIRECTORY_ORDER) == keys - {"online"}


def test_the_directory_columns_are_not_the_live_rosters():
    """The new columns live on a PAGE table: the dashboard's live roster is untouched."""
    live = view_layer.column_keys("players")
    directory = view_layer.column_keys("player_directory")
    assert {"playtime", "discord", "last_seen", "state"} <= set(directory)
    assert not {"playtime", "discord", "last_seen", "state"} & set(live), \
        "the dashboard's roster gained a column it cannot fill"
    assert view_layer.columns_for("player_directory"), "columns_for must find the page table"
