"""The punishment plugin's ban ladder: how many days a repeat offender gets.

``days`` stays a plain number for a fixed ban. A LIST turns it into a ladder, indexed by the bans of
that player which were SERVED inside ``reset_after_days``, so a player who stays clean long enough
starts over at the first entry.

Three pieces are pinned apart, so a change to one cannot hide behind the others:

* the ARITHMETIC — :func:`plugins.punishment.writes.escalated_days`, a pure function;
* the STATEMENT — which bans count as served: the plugin's own, lifted inside the player's last N
  FLOWN days, the one in force included and a ban lifted before it expired excluded;
* the JOIN — ``plugins.punishment.writes.ban_days``, which reads the count only when the ladder is a
  list and still bans (at the first entry) when the count cannot be read.

The schema is pinned last: the ladder and its window must be accepted, a plain number must keep
working, and a string must be refused before it can reach the code.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from core.utils import configwrite
from plugins.punishment import writes
from plugins.punishment.writes import escalated_days, served_ban_count

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The plugin's own schema plus the shared partials it includes (``commands_schema``,
#: ``chat_commands_schema``) — a plugin schema cannot be validated alone.
SCHEMAS = [str(REPO_ROOT / "plugins" / "punishment" / "schemas" / "punishment_schema.yaml"),
           str(REPO_ROOT / "schemas" / "commands_schema.yaml")]
LADDER = [3, 5, 7, 10]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. the arithmetic

def test_a_plain_number_is_that_many_days_whatever_the_history():
    """A fixed ban is the behaviour that existed before the ladder — it must not move."""
    assert escalated_days(3, 0) == 3
    assert escalated_days(3, 7) == 3


@pytest.mark.parametrize("prior, expected", [
    (0, 3),   # first ban
    (1, 5),   # second
    (2, 7),   # third
    (3, 10),  # fourth
    (4, 10),  # beyond the ladder — the last entry is the ceiling
    (99, 10),
    (-1, 3),  # a count can never be negative; it must not index from the end
])
def test_the_ladder_takes_the_entry_of_the_served_ban_count(prior, expected):
    assert escalated_days(LADDER, prior) == expected


def test_an_empty_or_missing_ladder_falls_back_to_the_default():
    """``days`` absent is the shipped default of 3, and an empty list is a config mistake, not a
    permanent ban: both answer the default rather than raising inside a ban."""
    assert escalated_days(None, 0) == writes.DEFAULT_BAN_DAYS
    assert escalated_days(0, 0) == writes.DEFAULT_BAN_DAYS
    assert escalated_days([], 4) == writes.DEFAULT_BAN_DAYS


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. the statement: which bans are "served"

class _Cursor:
    def __init__(self, value):
        self.value = value

    async def fetchone(self):
        return self.value


class _Conn:
    def __init__(self, recorder, value):
        self.recorder = recorder
        self.value = value

    async def execute(self, sql, params=None):
        self.recorder.append((" ".join(sql.split()), params))
        return _Cursor(self.value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """A connection pool double that records every statement it was handed."""

    def __init__(self, value=(0,)):
        self.calls = []
        self.value = value

    def connection(self):
        return _Conn(self.calls, self.value)


def test_the_count_reads_the_live_ban_and_the_archive_and_only_the_served_ones():
    """The predicate is the whole feature, so it is asserted on the SQL text.

    ``bans`` must be in the union (the ban in force is being served), ``bans_hist`` carries
    ``auto_unban`` (FALSE = lifted early = forgiven = does not count), and the DISTINCT is what keeps
    one ban spanning several servers from counting once per server.
    """
    sql = " ".join(writes.SERVED_BAN_COUNT_SQL.split())
    assert "count(DISTINCT b.banned_at)" in sql
    assert "FROM bans" in sql and "FROM bans_hist" in sql
    assert sql.count("SELECT ucid, banned_at, banned_until, banned_by") == 2, (
        "the outer WHERE reads ucid and banned_until, so BOTH sides of the union must select them")
    assert "TRUE AS served" in sql, "the ban in force is served"
    assert "coalesce(auto_unban, FALSE)" in sql, "a lifted ban must not count"
    assert "AND b.served" in sql, "the filter itself, not only the column"
    assert "banned_by = %s" in sql, "a manual or DGSA ban must not escalate the ladder"


def test_the_window_is_flown_days_counted_back_from_now():
    """The reset is measured in days the player actually FLEW, not in calendar days.

    The cutoff is the OLDEST of the player's last N flown days — ``DISTINCT hop_on::date`` so a day
    with ten sessions counts once — and ``-infinity`` when they never flew, which leaves every served
    ban counting rather than clearing a ladder nobody earned.
    """
    sql = " ".join(writes.SERVED_BAN_COUNT_SQL.split())
    assert "SELECT DISTINCT hop_on::date AS day FROM statistics" in sql, (
        "a flown day is a distinct calendar day with a session")
    assert "WHERE player_ucid = %s" in sql
    assert "ORDER BY day DESC LIMIT %s" in sql, "the LAST N flown days, not the first"
    assert "SELECT min(day) AS since" in sql, "the cutoff is the oldest of those N"
    assert "banned_until >= coalesce(flown.since, DATE '-infinity')" in sql, (
        "the window starts when the ban was LIFTED, and no flight at all clears nothing")


@pytest.mark.asyncio
async def test_the_count_is_parameterised_and_answers_the_number():
    pool = _Pool(value=(2,))
    assert await served_ban_count(pool, "abc", flight_days=14, banned_by="punishment") == 2
    sql, params = pool.calls[0]
    assert params == ("abc", 14, "abc", "punishment"), (
        "the cutoff is read for the same UCID the bans are counted for")
    assert "bans_hist" in sql


@pytest.mark.asyncio
async def test_an_empty_answer_counts_as_zero_bans():
    """A UCID with no ban row at all answers ``0`` from SUM-less COUNT — the row is never NULL, but a
    missing row must not raise either."""
    assert await served_ban_count(_Pool(value=None), "abc", flight_days=14,
                                  banned_by="punishment") == 0


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. the join: the count is read only when there is a ladder

@pytest.mark.asyncio
async def test_a_fixed_ban_never_reads_the_ban_history():
    """A config that did not opt into the ladder must not pay for a query — and must not change its
    answer when the database is unreachable."""
    pool = _Pool(value=(4,))
    assert await writes.ban_days(pool, "abc", {"action": "ban", "days": 3},
                                 plugin_name="punishment") == 3
    assert pool.calls == []


@pytest.mark.asyncio
async def test_the_ladder_uses_the_configured_window_and_the_served_count():
    pool = _Pool(value=(2,))
    assert await writes.ban_days(pool, "abc", {"action": "ban", "days": LADDER,
                                               "reset_after_days": 30},
                                 plugin_name="punishment") == 7
    assert pool.calls[0][1] == ("abc", 30, "abc", "punishment")


@pytest.mark.asyncio
async def test_the_window_defaults_when_the_rung_names_none():
    pool = _Pool(value=(1,))
    assert await writes.ban_days(pool, "abc", {"action": "ban", "days": LADDER},
                                 plugin_name="punishment") == 5
    assert pool.calls[0][1] == ("abc", writes.DEFAULT_BAN_RESET_FLIGHT_DAYS, "abc", "punishment")


@pytest.mark.asyncio
async def test_a_failed_count_still_bans_at_the_first_entry():
    """A ban that cannot be counted must still be raised: the alternative is a teamkiller nobody
    bans because a history read failed."""
    class _Broken(_Pool):
        def connection(self):
            raise RuntimeError("bans_hist is unavailable")

    assert await writes.ban_days(_Broken(), "abc", {"action": "ban", "days": LADDER},
                                 plugin_name="punishment") == 3


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 4. the schema: the ladder is config, and the shape is checked before the code sees it

@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """The validator resolves nodes.yaml/servers.yaml through ``COMMAND_LINE_ARGS.config``."""
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "servers.yaml").write_text("{}\n", encoding="utf-8")
    (directory / "nodes.yaml").write_text(
        "pytest:\n"
        "  public_ip: 127.0.0.1\n"
        "  instances:\n"
        "    DCS.dcs_serverrelease:\n"
        "      home: /home/dcs\n"
        "      bot_port: 6668\n"
        "      dcs_port: 10328\n"
        "      webgui_port: 8088\n",
        encoding="utf-8")
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(config=str(directory), node="pytest"),
                        raising=False)
    return directory


def _document(rung: str) -> str:
    return ("DEFAULT:\n"
            "  punishments:\n"
            + rung)


def test_a_ladder_and_its_window_are_accepted(config_dir):
    configwrite.validate_against_schema(
        _document("    - points: 100\n"
                  "      action: ban\n"
                  "      days: [3, 5, 7, 10]\n"
                  "      reset_after_days: 90\n"),
        [str(s) for s in SCHEMAS], path=config_dir / "punishment.yaml")


def test_a_plain_number_still_validates(config_dir):
    """The backward-compatible half: every existing install's ``days: 3`` keeps working."""
    configwrite.validate_against_schema(
        _document("    - points: 100\n"
                  "      action: ban\n"
                  "      days: 3\n"),
        [str(s) for s in SCHEMAS], path=config_dir / "punishment.yaml")


@pytest.mark.parametrize("rung", [
    "      days: '3'\n",                 # a quoted number is a string, not a day count
    "      days: [3, five]\n",          # a ladder of non-numbers
    "      days: [3, 5]\n      reset_after_days: 0\n",  # a window of zero days is not a window
])
def test_a_malformed_ban_length_is_refused(config_dir, rung):
    with pytest.raises(configwrite.ConfigValidationError):
        configwrite.validate_against_schema(
            _document("    - points: 100\n      action: ban\n" + rung),
            [str(s) for s in SCHEMAS], path=config_dir / "punishment.yaml")
