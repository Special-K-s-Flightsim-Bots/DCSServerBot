"""The ``mission_id:`` period is a number and must reach the statement as a number.

``missions.id`` is a ``SERIAL``, so ``MissionIDFilter`` parses the caller's value and re-renders
the parsed integer. Before that, the raw text was spliced into an unquoted ``m.id = …``; the only
cleaning was ``utils.sanitize_string``, which removes quotes, semicolons, backslashes and comment
markers — none of which an unquoted numeric position needs. The Discord ``/highscore`` transformer
excludes ``MissionIDFilter``, but ``GET /highscore?period=…`` (and the legacy chat parser, which
calls ``StatisticsFilter.detect`` on the raw words of a message) accepted it, so a caller could
append his own SQL to the WHERE clause of all nine statements the endpoint runs.

The endpoint is the primary caller, so it is driven here rather than only the filter: the point is
that no statement reaches the pool at all for a value that is not a number.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from plugins.restapi.commands import RestAPI
from plugins.userstats.filter import MissionIDFilter, StatisticsFilter

#: Values a caller could type after ``mission_id:`` that were previously concatenated verbatim.
#: None of them is a number, so the fix is that they never become SQL.
INJECTION_PAYLOADS = (
    "mission_id:1 OR 1=1",
    "mission_id:1) OR (1=1",
    "mission_id:0;DROP TABLE missions",
    "mission_id:$$1$$",
    "mission_id:chr(49)",
    "mission_id:1 --",
    "mission_id:",
    "mission_id:abc",
)


# --------------------------------------------------------------------------------------- doubles

class Cursor:
    def __init__(self, owner):
        self.owner = owner
        self.sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.owner.executed.append((self.sql, params))

    async def fetchall(self):
        return []


class Connection:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, **kwargs):
        return Cursor(self.owner)


class Pool:
    """The endpoint's async pool; records every statement the endpoint manages to build."""

    def __init__(self):
        self.executed: list[tuple[str, dict | None]] = []

    def connection(self):
        return Connection(self)


class SyncPool:
    """``core.utils.campaigns.get_all_campaigns`` reads campaigns on a SYNC connection."""

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return []


def build():
    api = object.__new__(RestAPI)
    api.apool = Pool()
    api.log = logging.getLogger("test.restapi.highscore")
    api.bot = SimpleNamespace(servers={}, node=SimpleNamespace(pool=SyncPool()))
    api.bus = SimpleNamespace(node=SimpleNamespace(instances={}))
    return api


# ------------------------------------------------------------------------------------------ pins

@pytest.mark.parametrize("period", INJECTION_PAYLOADS)
def test_a_value_that_is_not_a_number_never_becomes_sql(period):
    """The reported hole: the value went straight into an unquoted ``m.id = …``."""
    with pytest.raises(ValueError):
        MissionIDFilter(period).filter(None)


@pytest.mark.parametrize("period, expected", [
    ("mission_id:42", "m.id = 42"),
    ("mission_id:  42  ", "m.id = 42"),
    ("mission_id:+42", "m.id = 42"),
    ("mission_id:0007", "m.id = 7"),
])
def test_a_number_reaches_the_statement_as_its_parsed_integer(period, expected):
    assert MissionIDFilter(period).filter(None) == expected


@pytest.mark.parametrize("period", INJECTION_PAYLOADS)
@pytest.mark.asyncio
async def test_the_highscore_endpoint_runs_no_statement_for_an_injected_period(period):
    """Primary caller: the WHERE clause is built before ``execute()``, so a refusal means no SQL."""
    api = build()
    with pytest.raises(ValueError):
        await api.highscore(period=period)
    assert api.apool.executed == [], \
        f"a statement reached the database for {period!r}: {api.apool.executed}"


@pytest.mark.asyncio
async def test_the_highscore_endpoint_filters_a_real_mission_id():
    """Control: a genuine id still narrows all nine statements, and no prefix survives in them."""
    api = build()
    await api.highscore(period="mission_id:42", limit=10)
    statements = [sql for sql, _ in api.apool.executed]
    assert len(statements) == 9, statements
    for sql in statements:
        assert "m.id = 42" in sql, sql
        assert "mission_id:" not in sql, sql


@pytest.mark.asyncio
async def test_the_default_period_is_unaffected():
    """Control: the ordinary ``all`` call the website and every existing client makes."""
    api = build()
    await api.highscore(period="all", limit=10)
    statements = [sql for sql, _ in api.apool.executed]
    assert len(statements) == 9, statements
    assert all("1 = 1" in sql for sql in statements), statements


def test_detect_still_claims_the_prefix():
    """A malformed id must fail loudly, not fall through to a filter that widens the result."""
    bot = SimpleNamespace(servers={}, node=SimpleNamespace(pool=SyncPool()))
    assert isinstance(StatisticsFilter.detect(bot, "mission_id:1 OR 1=1"), MissionIDFilter)
