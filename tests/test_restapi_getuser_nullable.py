"""The REST API user lookup must answer for a player row that was never seen.

``players.last_seen`` is nullable: the cloud auto-link and the logbook migration both create a
named player without one. ``/getuser`` and ``/squadron_members`` read that column into
``UserEntry.date``, so a NULL used to raise a ValidationError out of the endpoint (HTTP 500).
The same lookup by ``discord_id`` also meets the 4-character link-token placeholder rows, which
have no name at all.
"""
from __future__ import annotations

import logging

import pytest

from plugins.restapi.commands import RestAPI
from plugins.restapi.models import UserEntry

REAL_UCID = "aabbccddeeffgghhiiffkk1234567890"
DISCORD_ID = 123456789012345678


class Cursor:
    """Stands in for the psycopg cursor: records SQL, answers with the given rows.

    The placeholder filter is applied here the way PostgreSQL applies it, by the predicate's
    name, so the row that the endpoint must ignore is really removed from the result.
    """

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

    def _rows(self):
        rows = list(self.owner.rows)
        if "length(ucid) > 4" in self.sql:
            rows = [row for row in rows if len(row["ucid"]) > 4]
        return rows

    async def fetchall(self):
        return self._rows()

    async def fetchone(self):
        return self._rows()[0] if self._rows() else None


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
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def connection(self):
        return Connection(self)


def build(rows):
    api = object.__new__(RestAPI)
    api.apool = Pool(rows)
    api.log = logging.getLogger("test.restapi.getuser")

    async def current_server(nick, date=None):
        return "Test Server"

    api.current_server = current_server
    return api


def row(**overrides):
    data = {"ucid": REAL_UCID, "discord_id": DISCORD_ID, "nick": "Special K", "date": None}
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_player_never_seen_is_returned_with_a_null_date():
    """The reported failure: the cloud auto-link row carries a name but no last_seen."""
    api = build([row(date=None)])
    result = await api.getuser(nick="Special K")
    assert len(result) == 1, result
    assert isinstance(result[0], UserEntry)
    assert result[0].date is None, "a never-seen player has no date, and that is not an error"
    assert '"date":null' in result[0].model_dump_json()


@pytest.mark.asyncio
async def test_link_token_placeholder_is_not_reported_as_a_user():
    """A 4-character UCID is a pending link token, not a player: it has no name."""
    api = build([row(date=None), row(ucid="1234", nick=None, date=None)])
    result = await api.getuser(discord_id=str(DISCORD_ID))
    assert [entry.ucid for entry in result] == [REAL_UCID], result
    sql, _ = api.apool.executed[0]
    assert "length(ucid) > 4" in sql, sql


@pytest.mark.asyncio
async def test_a_seen_player_keeps_its_timestamp():
    """Control: a healthy row still carries the parsed last_seen value."""
    from datetime import datetime

    api = build([row(date=datetime(2026, 11, 23, 14, 15, 8))])
    result = await api.getuser(nick="Special K")
    assert result[0].date == datetime(2026, 11, 23, 14, 15, 8), result[0]