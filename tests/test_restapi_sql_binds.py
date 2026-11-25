"""The REST statements that carried caller text must carry bound parameters instead.

Covers the four SQL fixes of the 2026-11-24 audit of `plugins/restapi`:

* `/events` — `event` and `init_type` were concatenated into the WHERE clause with no cleaning at
  all (`AND event = '{event}'`), so `' OR 1=1 --` dropped the UCID/time-window restriction and a
  `UNION SELECT` could carry any column out through `EventEntry`.
* `/serverstats` — the operator's `filter.mission_name` regex was spliced into a SQL literal.
* `/leaderboard` — the search term was passed as a parameter the statement never used, and applied
  in Python *after* `LIMIT`, so a search returned the wrong rows and a `total_count` that ignored it.
* `/linkme` — the 4-digit link token came from `random`, which is predictable.

Each case drives the real endpoint method with a pool that records what would have been sent.
"""
from __future__ import annotations

import datetime
import logging
import secrets
from types import SimpleNamespace

import pytest

from plugins.restapi.commands import RestAPI

UCID = "aabbccddeeffgghhiiffkk1234567890"


class Cursor:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.owner.executed.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.owner.one

    async def fetchall(self):
        return self.owner.many

    def __aiter__(self):
        self._it = iter(self.owner.many)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class Connection:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, **kwargs):
        return Cursor(self.owner)

    async def execute(self, sql, params=None):
        self.owner.executed.append((" ".join(sql.split()), params))
        return Cursor(self.owner)


class Pool:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = many if many is not None else []
        self.executed: list[tuple[str, object]] = []

    def connection(self):
        return Connection(self)

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.executed]


def build(one=None, many=None) -> RestAPI:
    api = object.__new__(RestAPI)
    api.apool = Pool(one, many)
    api.log = logging.getLogger("tests.restapi.sql")
    api.bot = SimpleNamespace(servers={}, filter={"mission_name": "[.*]"},
                              node=SimpleNamespace(pool=Pool()))
    api.bus = SimpleNamespace(node=SimpleNamespace(instances={}))
    return api


# ------------------------------------------------------------------------------------ /events

EVENT_PAYLOADS = (
    "' OR 1=1 --",
    "x' UNION SELECT null, null, ucid, null, null, null, null, null, null, null, null, null, null FROM players --",
    "S_EVENT_KILL' OR '1'='1",
)


@pytest.mark.parametrize("payload", EVENT_PAYLOADS)
@pytest.mark.asyncio
async def test_an_event_filter_never_reaches_the_statement_as_text(payload):
    api = build()
    await api.events(ucid=UCID, start_time=datetime.datetime(2026, 1, 1),
                     end_time=datetime.datetime(2026, 12, 31), event=payload, init_type=payload,
                     offset=0, limit=10)
    sql, params = api.apool.executed[0]
    assert payload not in sql, sql
    assert "AND event = %(event)s" in sql, sql
    assert "AND init_type = %(init_type)s" in sql, sql
    assert params["event"] == payload and params["init_type"] == payload, params
    # the restrictions an injected OR used to defeat are still there, unconditionally
    assert "(init_id = %(ucid)s or target_id = %(ucid)s)" in sql, sql
    assert "time between %(start_time)s AND %(end_time)s" in sql, sql


# -------------------------------------------------------------------------------- /serverstats

@pytest.mark.asyncio
async def test_the_mission_name_regex_is_a_bound_parameter():
    """The operator's filter regex is a value, not SQL — a quote in the config must not end the literal."""
    aggregate = {key: 0 for key in (
        "unique_players", "total_playtime_hours", "discord_members",
        "totalPlayers", "totalPlaytime", "avgPlaytime", "totalSorties", "totalKills",
        "totalDeaths", "totalPvPKills", "totalPvPDeaths",
        "total_sorties", "total_kills", "total_deaths", "total_pvp_kills", "total_pvp_deaths")}
    api = build(one=aggregate)
    api.bot.filter = {"mission_name": "[.*]'"}
    await api.server_attendance(server_name=None)
    matches = [(sql, p) for sql, p in api.apool.executed if "regexp_replace" in sql]
    assert len(matches) == 1, api.apool.statements()
    statement, params = matches[0]
    assert "regexp_replace(m.mission_name, %(mission_filter)s" in statement, statement
    assert params["mission_filter"] == "[.*]'", params


# -------------------------------------------------------------------------------- /leaderboard

@pytest.mark.asyncio
async def test_the_search_term_is_filtered_by_the_statement_not_after_the_limit():
    row = {"nick": "Special K", "date": datetime.datetime(2026, 11, 24, 12, 0, 0), "kills": 1,
           "kills_pvp": 0, "deaths": 0, "kdr": 1,
           "deaths_pvp": 0, "kdr_pvp": 0, "playtime": 10, "credits": 0, "row_num": 1,
           "total_count": 1}
    api = build(many=[dict(row)])
    result = await api.leaderboard("kills", "desc", "zzz-no-match", 10, 0, None)
    statement, params = api.apool.executed[0]
    assert "p.name ILIKE %(query)s" in statement, statement
    assert params["query"] == "%zzz-no-match%", params
    # the row the stub returned is returned; the old code dropped it in Python after the LIMIT
    assert [item.nick for item in result.items] == ["Special K"], result
    assert result.total_count == 1


@pytest.mark.asyncio
async def test_no_search_term_still_asks_for_every_player():
    api = build(many=[])
    await api.leaderboard("kills", "desc", None, 10, 0, None)
    _, params = api.apool.executed[0]
    assert params["query"] is None, params


# ------------------------------------------------------------------------------------- /linkme

@pytest.mark.asyncio
async def test_the_link_token_comes_from_the_system_csprng(monkeypatch):
    """Same 4-digit shape (four modules spot a pending link by `length(ucid) = 4`), unpredictable source."""
    monkeypatch.setattr(secrets, "randbelow", lambda n: n - 1)
    api = build(one=None)
    response = await api.linkme(discord_id="123456789012345678", force=False)
    assert response.token == "9999", response.token
    assert len(response.token) == 4
