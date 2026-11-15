"""Per-server ban scope: the ``server_name`` column, its ONE predicate, and who writes which row.

A ban is stored as one row per covered server; ``server_name IS NULL`` is the bot-wide ban. The
scope is decided at the ENTRY POINT (never at read time):

* a ban a PERSON issues covers every server whose ``managed_by`` names one of the issuer's roles —
  an issuer that holds no ``managed_by`` role anywhere is unscoped and bans bot-wide;
* a ban a SERVER EVENT raises covers every server sharing that server's ``managed_by``, and a
  server that declares none bans bot-wide;
* every other entry point (DGSA sync, a Discord ban, "player left discord") stays bot-wide.

Enforcement is one predicate — ``(server_name IS NULL OR server_name = %s)`` — applied by the
mission listener's ban push and by ``is_banned``; ``Member.prep`` reads GLOBAL bans only, so a
scoped ban never marks a member banned on Discord.
"""
from __future__ import annotations

import asyncio
import importlib
import re
import sys
import types
from pathlib import Path

import pytest

from core import Status
from plugins.admin.commands import _ban_targets as admin_targets
from plugins.admin.commands import _ban_scope_label as admin_label
from plugins.mission.actions import _ban_scope_label as action_label
from plugins.mission.actions import _caller_ban_targets
from plugins.mission.views import ban_scope_label as view_label
from plugins.mission.views import ban_targets as view_targets
from plugins.voting.options.tempban import _ban_targets as event_targets
from services.servicebus import ServiceBus
from services.webservice.scope import Scope

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "plugins" / "mission" / "db" / "update_v3.20.sql"

#: The bot's own configured Admin role (``bot.roles['Admin']``): its default, and what every
#: double that stands in for a live bot declares.
ADMIN_ROLES = ["Admin"]


# ------------------------------------------------------------------------------- test doubles

class _Role:
    def __init__(self, name: str, role_id: int):
        self.name = name
        self.id = role_id


class _Member:
    """A caller as the scope rule reads it: a role list of names AND ids (``@everyone`` included)."""

    def __init__(self, *names: str):
        self.roles = [_Role("@everyone", 0)] + [_Role(name, 100 + i) for i, name in enumerate(names)]
        self.display_name = ", ".join(names) if names else "@everyone"


class _Server:
    def __init__(self, name: str, managed_by=()):
        self.name = name
        self.display_name = name
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}


def _fleet():
    """Three servers: two tagged by different hosts, one UNTAGGED."""
    return {
        "SRS-1": _Server("SRS-1", ["Host A"]),
        "SRS-B": _Server("SRS-B", ["Host B"]),
        "SRS-U": _Server("SRS-U"),
    }


class _Cursor:
    """Records every statement; serves the configured rows for iteration and ``fetchone``."""

    rowcount = 0

    def __init__(self, rows=(), one=None):
        self.rows = list(rows)
        self.one = one
        self.queries: list[tuple] = []

    async def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params))
        return self

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return list(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for row in self.rows:
                yield row
        return gen()

    @property
    def last(self):
        return self.queries[-1]


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **kwargs):
        return self._cursor

    async def execute(self, sql, params=None):
        return await self._cursor.execute(sql, params)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, cursor):
        self._cursor = cursor

    def connection(self):
        return _Conn(self._cursor)


def _bus(cursor):
    """A ``ServiceBus`` whose pool is a recorder — no database, no node."""
    bus = object.__new__(ServiceBus)
    bus.apool = _Pool(cursor)
    sent: list[dict] = []

    async def send_to_node(payload):
        sent.append(payload)

    bus.send_to_node = send_to_node
    bus.sent = sent
    return bus


# --------------------------------------------------------------- who writes which row (issuer)

def test_a_restricted_issuer_gets_exactly_their_tagged_servers():
    """SRS-U is untagged, so it is NOT the issuer's — their ban must not reach it."""
    servers = _fleet()
    hoster = _Member("Host A")

    assert admin_targets(hoster, servers) == ["SRS-1"]
    assert view_targets(hoster, servers) == ["SRS-1"]
    assert admin_label(["SRS-1"]) == "SRS-1"


def test_an_issuer_whose_roles_tag_several_servers_gets_one_row_each():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}

    assert admin_targets(_Member("Host A"), servers) == ["A", "B"]
    assert view_targets(_Member("Host A"), servers) == ["A", "B"]
    assert admin_label(["A", "B"]) == "A, B"
    assert view_label(["A", "B"]) == "A, B"


def test_an_unscoped_issuer_bans_bot_wide():
    """No server names the issuer's roles: the ban is bot-wide, which is TODAY's behaviour."""
    servers = _fleet()
    admin = _Member("Admin", "DCS Admin")

    assert admin_targets(admin, servers) == []
    assert view_targets(admin, servers) == []
    assert admin_label([]) == "all servers"
    assert view_label([]) == "all servers"


def test_a_role_id_tags_a_server_like_its_name():
    """A ``managed_by`` list may mix ids and names, and both must match."""
    member = _Member("Host A")
    servers = {"A": _Server("A", [member.roles[1].id]), "B": _Server("B", [99999])}
    assert admin_targets(member, servers) == ["A"]


# ----------------------------------------------------- the Admin door: one rule for every member
#: ``core.utils.discord.member_ban_targets`` is the ONE member-derived ban scope. An issuer holding
#: the bot's Admin role is UNRESTRICTED — the ban is bot-wide — even when their roles also tag a
#: server; every other issuer keeps the ``managed_by`` rule. Naming a server stays the caller's own.


def _shared_rule():
    """The rule at its ONE home, imported lazily: an absent name fails INSIDE the test that wants
    it, so the behavioural RED of the command/view tests below stays an assertion."""
    from core.utils.discord import member_ban_targets
    return member_ban_targets


def test_the_shared_rule_makes_an_admin_unrestricted():
    """An Admin whose roles also tag exactly ONE server bans bot-wide — the reported one-row symptom."""
    servers = {"A": _Server("A", ["Admin", "Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    rule = _shared_rule()

    assert rule(_Member("Admin", "Host A"), servers, ADMIN_ROLES) == []
    # the SAME set an issuer whose roles tag nothing gets: both are the bot-wide set
    assert rule(_Member("Admin", "Host A"), servers, ADMIN_ROLES) == \
        rule(_Member("Nobody"), servers, ADMIN_ROLES)


def test_without_the_admin_role_the_managed_by_rule_still_decides():
    """A member that does not hold the bot's Admin role covers exactly the servers their roles tag."""
    servers = _fleet()

    assert _shared_rule()(_Member("Host A"), servers, ADMIN_ROLES) == ["SRS-1"]
    assert _shared_rule()(_Member("Host B"), servers, ADMIN_ROLES) == ["SRS-B"]
    # a role NAME "Admin" is not the bot's Admin role when the bot's Admin role is configured as another role
    assert _shared_rule()(_Member("Admin"), servers, ["Floor Manager"]) == []


def test_the_admin_role_may_be_configured_as_a_role_id():
    """``bot.roles['Admin']`` may name the role by id: the member's role ids carry the match."""
    member = _Member("Floor Manager")
    servers = {"A": _Server("A", [member.roles[1].name]), "U": _Server("U")}

    assert _shared_rule()(member, servers, [member.roles[1].id]) == []


# ----------------------------------------------------------------- who writes which row (event)

def test_a_server_event_ban_follows_the_servers_own_scope():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}

    assert event_targets(_Server("A", ["Host A"]), servers) == ["A", "B"]
    assert event_targets(_Server("U"), servers) == []


# --------------------------------------------------------------- who writes which row (console)

def _ctx(scope, servers):
    return types.SimpleNamespace(_scope=scope, servers=servers)


def test_the_console_caller_bans_on_its_own_view():
    servers = _fleet()

    assert _caller_ban_targets(_ctx(Scope.restricted(["Host A"]), servers)) == ["SRS-1"]
    assert action_label(["SRS-1"]) == "SRS-1"


def test_an_unscoped_or_unknown_console_caller_bans_bot_wide():
    servers = _fleet()

    assert _caller_ban_targets(_ctx(Scope.everything(), servers)) == []
    assert _caller_ban_targets(_ctx(None, servers)) == []
    assert action_label([]) == "all servers"


# ---------------------------------------------------------------------- the ServiceBus predicate

@pytest.mark.asyncio
async def test_ban_writes_the_scope_and_the_conflict_key_and_announces_it():
    cursor = _Cursor()
    bus = _bus(cursor)

    await bus.ban("U1", "admin", "reason", 5, server_name="SRS-1")
    await asyncio.sleep(0)

    sql, params = cursor.last
    assert "INSERT INTO bans (ucid, banned_by, reason, banned_at, banned_until, server_name)" in sql
    assert "ON CONFLICT (ucid, COALESCE(server_name, '')) DO UPDATE" in sql
    assert params[-1] == "SRS-1"
    assert bus.sent[-1]["params"]["data"]["server_name"] == "SRS-1"


@pytest.mark.asyncio
async def test_a_bot_wide_ban_writes_null_and_announces_null():
    cursor = _Cursor()
    bus = _bus(cursor)

    await bus.ban("U1", "admin", "reason")
    await asyncio.sleep(0)

    assert cursor.last[1][-1] is None
    assert bus.sent[-1]["params"]["data"]["server_name"] is None


@pytest.mark.asyncio
async def test_unban_without_a_scope_clears_every_row_of_that_ucid():
    cursor = _Cursor()
    bus = _bus(cursor)

    await bus.unban("U1")
    await asyncio.sleep(0)

    sql, params = cursor.last
    assert "server_name" not in sql
    assert params == ("U1", )
    assert bus.sent[-1]["params"]["data"]["server_name"] is None


@pytest.mark.asyncio
async def test_unban_with_a_scope_clears_only_that_servers_row():
    cursor = _Cursor()
    bus = _bus(cursor)

    await bus.unban("U1", "SRS-1")
    await asyncio.sleep(0)

    sql, params = cursor.last
    assert sql.endswith("AND server_name = %s")
    assert params == ("U1", "SRS-1")
    assert bus.sent[-1]["params"]["data"]["server_name"] == "SRS-1"


@pytest.mark.asyncio
async def test_is_banned_matches_the_servers_own_ban_or_a_bot_wide_one():
    cursor = _Cursor(one=None)
    bus = _bus(cursor)

    await bus.is_banned("U1", "SRS-1")
    sql, params = cursor.last
    assert "(server_name IS NULL OR server_name = %s)" in sql
    assert params == ("U1", "SRS-1")


@pytest.mark.asyncio
async def test_is_banned_without_a_server_context_matches_any_ban():
    cursor = _Cursor(one=None)
    bus = _bus(cursor)

    await bus.is_banned("U1")
    sql, params = cursor.last
    assert "server_name" not in sql
    assert params == ("U1", )


@pytest.mark.asyncio
async def test_bans_groups_the_rows_of_one_ban_back_into_one_entry():
    """N server rows of ONE ban (same ucid, banned_at, reason) are ONE ban with a scope list."""
    stamp = object()
    rows = [
        {"ucid": "U1", "discord_id": -1, "name": "N", "banned_by": "admin", "reason": "r",
         "banned_at": stamp, "banned_until": stamp, "server_name": "A"},
        {"ucid": "U1", "discord_id": -1, "name": "N", "banned_by": "admin", "reason": "r",
         "banned_at": stamp, "banned_until": stamp, "server_name": "B"},
        {"ucid": "U2", "discord_id": -1, "name": "N2", "banned_by": "admin", "reason": "r2",
         "banned_at": stamp, "banned_until": stamp, "server_name": None},
    ]
    cursor = _Cursor(rows)
    bus = _bus(cursor)

    result = await bus.bans()

    assert [(b["ucid"], b["server_name"]) for b in result] == [("U1", ["A", "B"]), ("U2", None)]
    assert "b.server_name" in cursor.last[0]


@pytest.mark.asyncio
async def test_bans_keeps_two_bans_of_one_player_apart():
    """Different banned_at (a re-ban) is a SEPARATE ban, not a wider scope for the old one."""
    older, newer = object(), object()
    rows = [
        {"ucid": "U1", "discord_id": -1, "name": "N", "banned_by": "admin", "reason": "r",
         "banned_at": older, "banned_until": older, "server_name": "A"},
        {"ucid": "U1", "discord_id": -1, "name": "N", "banned_by": "admin", "reason": "r",
         "banned_at": newer, "banned_until": newer, "server_name": "B"},
    ]
    bus = _bus(_Cursor(rows))

    result = await bus.bans()

    assert [b["server_name"] for b in result] == [["A"], ["B"]]


# ------------------------------------------------------------- the listener's push is scoped too

def _plugin_module(name: str):
    """Import a plugin module with a stub for an absent optional dependency."""
    stub = {mod: types.ModuleType(mod) for mod in ("openpyxl", "openpyxl.utils", "trueskill")}
    stub["openpyxl.utils"].get_column_letter = lambda n: "A"
    stub["openpyxl"].utils = stub["openpyxl.utils"]
    stub["trueskill"].TrueSkill = lambda *args, **kwargs: None
    stub["trueskill"].Rating = type("Rating", (), {})
    stub["trueskill"].BETA = 0
    stub["trueskill"].global_env = None
    added = {k: v for k, v in stub.items() if k not in sys.modules}
    sys.modules.update(added)
    try:
        return importlib.import_module(name)
    finally:
        for mod in added:
            sys.modules.pop(mod, None)


def test_the_profanity_and_evade_buttons_use_the_servers_own_scope():
    """The buttons name the server they were raised on; one posted before that bans bot-wide."""
    module = _plugin_module("plugins.mission.commands")
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}

    assert module._ban_targets(_Server("A", ["Host A"]), servers) == ["A", "B"]
    assert module._ban_targets(_Server("U"), servers) == [None]
    assert module._ban_targets(None, servers) == [None]


@pytest.mark.asyncio
async def test_the_ban_push_asks_for_this_servers_bans_only():
    module = _plugin_module("plugins.mission.listener")
    cursor = _Cursor(rows=[])
    listener = object.__new__(module.MissionEventListener)
    listener.apool = _Pool(cursor)
    pushes: list[dict] = []

    class _Srv:
        name = "SRS-1"

        async def send_to_dcs(self, payload):
            pushes.append(payload)

    await module.MissionEventListener._update_bans(listener, _Srv())

    sql, params = cursor.last
    assert "server_name IS NULL OR server_name = %s" in sql
    assert params == ("SRS-1", )
    assert pushes == []


@pytest.mark.asyncio
async def test_the_ban_push_sends_only_the_rows_the_database_returned_for_this_server():
    module = _plugin_module("plugins.mission.listener")
    cursor = _Cursor(rows=[{"ucid": "U1", "reason": "r", "banned_until": _Never()}])
    listener = object.__new__(module.MissionEventListener)
    listener.apool = _Pool(cursor)
    pushes: list[dict] = []

    class _Srv:
        name = "SRS-1"

        async def send_to_dcs(self, payload):
            pushes.append(payload)

    await module.MissionEventListener._update_bans(listener, _Srv())

    assert pushes == [{"command": "ban", "batch": [{"ucid": "U1", "reason": "r",
                                                    "banned_until": "never"}]}]


class _Never:
    """A ``banned_until`` far in the future — ``_update_bans`` reads only its year."""

    year = 9999


# ---------------------------------------------------------------- the in-game chat /ban is scoped

def _chat_listener(servers: dict):
    """The chat listener as ``/ban`` uses it: the real ``_handle_command`` bound to a recorder."""
    module = _plugin_module("plugins.mission.listener")
    listener = types.SimpleNamespace(prefix="-", ban=module.MissionEventListener.ban)
    written: list[dict] = []

    async def ban(ucid, banned_by, reason, days, server_name=None, banned_at=None):
        written.append(dict(ucid=ucid, banned_by=banned_by, reason=reason, days=days,
                            server_name=server_name, banned_at=banned_at))

    listener.bus = types.SimpleNamespace(servers=servers, ban=ban)
    listener.bot = types.SimpleNamespace(audit=lambda *args, **kwargs: asyncio.sleep(0))
    listener._handle_command = types.MethodType(module.MissionEventListener._handle_command, listener)
    return module, listener, written


class _ChatServer(_Server):
    """A server as the chat handler reads it: ``get_player`` resolves the named delinquent."""

    def get_player(self, **kwargs):
        return types.SimpleNamespace(ucid="U1", name="Doe", display_name="Doe")


def _chat_server(name: str, managed_by=()):
    return _ChatServer(name, managed_by)


def _chat_player():
    player = types.SimpleNamespace(ucid="P1", name="Admin", display_name="Admin", member=None)
    sent: list[str] = []

    async def sendChatMessage(msg):
        sent.append(msg)

    player.sendChatMessage = sendChatMessage
    player.sent = sent
    return player


@pytest.mark.asyncio
async def test_the_chat_ban_writes_one_row_per_covered_server_and_no_global_row():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}
    module, listener, written = _chat_listener(servers)
    player = _chat_player()

    await module.MissionEventListener.ban.callback(listener, _chat_server("A", ["Host A"]),
                                                   player, ["Doe", "cheating"])
    await asyncio.sleep(0)

    assert [w["server_name"] for w in written] == ["A", "B"]
    assert all(w["server_name"] is not None for w in written)
    assert len({w["banned_at"] for w in written}) == 1
    assert all(w["ucid"] == "U1" and w["banned_by"] == "Admin" and w["reason"] == "cheating"
               and w["days"] == 3 for w in written)
    assert player.sent == ["User Doe banned for 3 days"]


@pytest.mark.asyncio
async def test_the_chat_ban_on_an_untagged_server_writes_a_single_global_row():
    servers = {"A": _Server("A", ["Host A"]), "U": _Server("U")}
    module, listener, written = _chat_listener(servers)
    player = _chat_player()

    await module.MissionEventListener.ban.callback(listener, _chat_server("U"), player, ["Doe"])
    await asyncio.sleep(0)

    assert [w["server_name"] for w in written] == [None]
    assert player.sent == ["User Doe banned for 3 days"]


# ------------------------------------------------------------------------- schema and migration

def test_the_migration_is_named_for_the_version_it_migrates_from():
    """The loader runs ``update_v<installed>.sql``, so the file must name the OLD version."""
    version = re.search(r'__version__ = "([^"]+)"',
                        (REPO / "plugins" / "mission" / "version.py").read_text(encoding="utf-8"))
    assert version, "plugins/mission/version.py carries no __version__"
    assert MIGRATION.name == "update_v3.20.sql"
    assert MIGRATION.exists()
    # the loader steps one revision per file, so the installed version is exactly the revision
    # above the newest migration file of this chain
    newest = max((path for path in MIGRATION.parent.glob("update_v3.*.sql")),
                 key=lambda path: int(path.stem.split(".")[1]))
    assert int(newest.stem.split(".")[1]) + 1 == int(version.group(1).split(".")[1])


def test_the_migration_replaces_the_primary_key_with_the_scope_unique_index():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE bans ADD COLUMN server_name TEXT;" in sql
    assert "ALTER TABLE bans DROP CONSTRAINT bans_pkey;" in sql
    assert "CREATE UNIQUE INDEX bans_ucid_server_uidx ON bans (ucid, COALESCE(server_name, ''));" in sql


def test_the_migration_carries_the_scope_into_the_history():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE bans_hist ADD COLUMN server_name TEXT;" in sql
    assert "ALTER TABLE bans_hist DROP CONSTRAINT bans_hist_pkey;" in sql
    assert "CREATE UNIQUE INDEX bans_hist_ucid_banned_at_server_uidx" in sql
    assert "ON bans_hist (ucid, banned_at, COALESCE(server_name, ''));" in sql
    assert "INSERT INTO bans_hist(ucid, banned_by, reason, banned_at, banned_until, auto_unban, server_name)" in sql
    assert "OLD.server_name" in sql


def test_a_fresh_install_gets_the_same_bans_shape_as_the_migration():
    """``tables.sql`` is what a NEW install gets, so it must carry the column, the index and the
    history column too — otherwise the two paths disagree."""
    sql = (REPO / "plugins" / "mission" / "db" / "tables.sql").read_text(encoding="utf-8")
    assert "server_name TEXT" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS bans_ucid_server_uidx ON bans (ucid, COALESCE(server_name, ''));" in sql
    # the history's uniqueness must carry the scope too, or a grouped ban keeps only its first row
    assert "auto_unban BOOLEAN DEFAULT FALSE,\n    server_name TEXT\n);" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS bans_hist_ucid_banned_at_server_uidx" in sql
    assert "ON bans_hist (ucid, banned_at, COALESCE(server_name, ''));" in sql
    assert "OLD.server_name" in sql


# ------------------------------------------------------------------------ the prep queries are scoped

def _member_double(cursor):
    """A real ``Member`` whose pool is a recorder — ``prep`` needs only ``member.id``."""
    from core.data.member import Member

    member = object.__new__(Member)
    member.apool = _Pool(cursor)
    member.member = types.SimpleNamespace(id=123)
    member._ucid = None
    member.banned = False
    member._verified = False
    return member


@pytest.mark.asyncio
async def test_member_prep_reads_only_bot_wide_bans():
    """``Member.prep`` must never see a scoped row: the join pins ``b.server_name IS NULL``."""
    cursor = _Cursor(one=None)
    member = _member_double(cursor)

    await member.prep()

    sql, params = cursor.last
    assert "b.server_name IS NULL" in sql
    assert "b.server_name = %s" not in sql
    assert params == (123, )


def _player_double(cursor, server_name="SRS-1"):
    """A real ``Player`` whose pool is a recorder — the caller is already resolved, so no automatch."""
    from core.data.player import Player

    player = object.__new__(Player)
    player.id = 2
    player.ucid = "U1"
    player.name = "N"
    player.apool = _Pool(cursor)
    player.server = types.SimpleNamespace(name=server_name, locals={"coalitions": {}})
    player._member = types.SimpleNamespace(id=123)

    async def get_member_by_ucid(ucid):
        return None

    player.bot = types.SimpleNamespace(get_member_by_ucid=get_member_by_ucid, locals={})
    return player


@pytest.mark.asyncio
async def test_player_prep_binds_this_servers_own_name_in_the_scope_predicate():
    """``Player.prep`` carries the ONE predicate, with THIS server's name bound (twice)."""
    cursor = _Cursor(one=None)
    player = _player_double(cursor)

    await player.prep()

    sql, params = cursor.last
    assert "AND (b.server_name IS NULL OR b.server_name = %s)" in sql
    assert params == ("SRS-1", "SRS-1", "U1")


# ---------------------------------------------------- check_for_unban releases ONE row at a time

class _UnbanPlayer:
    def __init__(self, ucid):
        self.ucid = ucid
        self.banned = True


class _UnbanServer:
    """The slice of ``Server`` that ``check_for_unban`` touches."""

    def __init__(self, name, ucid=None):
        self.name = name
        self.display_name = name
        self.status = Status.RUNNING
        self._players = {ucid: _UnbanPlayer(ucid)} if ucid else {}
        self.sent: list[dict] = []

    def get_player(self, ucid=None):
        return self._players.get(ucid)

    async def send_to_dcs(self, payload):
        self.sent.append(payload)


def _mission_double(cursor, servers):
    # ``_plugin_module`` stubs the absent openpyxl so this import works in isolation too (a bare
    # ``from plugins.mission.commands import Mission`` only succeeds once the module is cached by an
    # earlier test in this file — which made these tests order-dependent).
    module = _plugin_module("plugins.mission.commands")
    mission = object.__new__(module.Mission)
    mission.apool = _Pool(cursor)
    mission.bot = types.SimpleNamespace(servers=servers)
    return mission


@pytest.mark.asyncio
async def test_check_for_unban_deletes_only_the_expired_row_and_releases_only_its_server():
    """A scoped row expiring on A must not delete A's SIBLING bot-wide row, nor unban B.

    The pre-fix loop deleted ``WHERE ucid = %s`` (every row) and pushed unban to every server — this
    is the BLOCKING defect the review raised."""
    servers = {"A": _UnbanServer("A", "U1"), "B": _UnbanServer("B", "U1")}
    # the SELECT returns the expired scoped row of A; the active bot-wide row is NOT selected
    cursor = _Cursor(rows=[("U1", "A")])
    mission = _mission_double(cursor, servers)

    await mission.check_for_unban()

    # the SELECT must carry the scope so the row's server is known
    select_sql = cursor.queries[0][0]
    assert "SELECT ucid, server_name FROM bans" in select_sql
    # delete carries the same predicate, binding the row's own server_name
    delete_sql, delete_params = cursor.last
    assert "DELETE FROM bans" in delete_sql
    assert "AND banned_until < (NOW() AT TIME ZONE 'utc')" in delete_sql
    assert "AND server_name IS NOT DISTINCT FROM %s" in delete_sql
    assert delete_params == ("U1", "A")
    # only A is released; B's active ban survives
    assert servers["A"].sent == [{"command": "unban", "ucid": "U1"}]
    assert servers["B"].sent == []
    assert servers["A"].get_player("U1").banned is False
    assert servers["B"].get_player("U1").banned is True


@pytest.mark.asyncio
async def test_check_for_unban_releases_every_server_for_an_expired_global_row():
    """A NULL row is the bot-wide ban: its expiry releases every server."""
    servers = {"A": _UnbanServer("A", "U1"), "B": _UnbanServer("B", "U1")}
    cursor = _Cursor(rows=[("U1", None)])
    mission = _mission_double(cursor, servers)

    await mission.check_for_unban()

    assert servers["A"].sent == [{"command": "unban", "ucid": "U1"}]
    assert servers["B"].sent == [{"command": "unban", "ucid": "U1"}]
    assert servers["A"].get_player("U1").banned is False
    assert servers["B"].get_player("U1").banned is False
    assert cursor.last[1] == ("U1", None)


import discord


# ------------------------------------------------------------- the rowcount an unban reports back

class _CountingCursor(_Cursor):
    """A recorder that answers with a fixed ``rowcount``, the way a real UPDATE cursor does."""

    def __init__(self, rowcount=0):
        super().__init__()
        self.rowcount = rowcount


@pytest.mark.asyncio
async def test_unban_reports_how_many_rows_it_cleared():
    bus = _bus(_CountingCursor(rowcount=2))

    assert await bus.unban("U1") == 2
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_unban_reports_zero_when_no_row_is_inside_the_scope():
    bus = _bus(_CountingCursor(rowcount=0))

    assert await bus.unban("U1", "SRS-1") == 0
    await asyncio.sleep(0)


# ------------------------------------------------------------ the entry points clear their scope

class _ReplyInteraction:
    """The slice of an interaction the unban entry points touch, recording the reply text."""

    def __init__(self, user):
        self.user = user
        self.channel = None
        self.client = types.SimpleNamespace(get_server=lambda _interaction: None)
        self.sent: list[str] = []
        self.response = types.SimpleNamespace(send_message=self._send, defer=self._defer)
        self.followup = types.SimpleNamespace(send=self._send)

    async def _defer(self):
        return None

    async def _send(self, message, ephemeral=False):
        self.sent.append(message)


def _admin_unban(servers, cleared=None):
    """The real ``/dcs unban`` callback bound to a recorder instead of a live cog."""
    module = sys.modules["plugins.admin.commands"]
    plugin = object.__new__(module.Admin)
    calls: list[tuple] = []
    audits: list[str] = []
    cleared = cleared or {}

    async def unban(ucid, server_name=None):
        calls.append((ucid, server_name))
        return cleared.get(server_name, 0)

    async def get_member_or_name_by_ucid(ucid):
        return "Doe"

    async def audit(message, *args, **kwargs):
        audits.append(message)

    plugin.bus = types.SimpleNamespace(unban=unban)
    plugin.bot = types.SimpleNamespace(servers=servers, audit=audit, roles={"Admin": ADMIN_ROLES},
                                       get_member_or_name_by_ucid=get_member_or_name_by_ucid)
    plugin.audit_lines = audits
    return module, plugin, calls


@pytest.mark.asyncio
async def test_a_narrowed_issuers_unban_clears_only_their_own_servers_rows():
    """A ``managed_by``-narrowed issuer must not reach the bot-wide row (nor another host's)."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers, {"A": 1})
    interaction = _ReplyInteraction(_Member("Host A"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", "A")]
    assert interaction.sent == ["Player Doe unbanned on A."]


@pytest.mark.asyncio
async def test_an_unscoped_admin_unban_clears_every_row_of_the_ucid():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers, {None: 2})
    interaction = _ReplyInteraction(_Member("Admin", "DCS Admin"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", None)]
    assert interaction.sent == ["Player Doe unbanned on all servers."]


@pytest.mark.asyncio
async def test_an_unban_with_nothing_in_scope_says_so_instead_of_unbanned():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers)
    interaction = _ReplyInteraction(_Member("Host A"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", "A")]
    assert interaction.sent == ["Player Doe was not banned on A."]


@pytest.mark.asyncio
async def test_an_unban_with_nothing_in_scope_records_no_unban_in_the_audit_trail():
    """The audit entry must state that nothing was in scope, not claim an unban happened."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers)
    interaction = _ReplyInteraction(_Member("Host A"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", "A")]
    assert plugin.audit_lines == ["did not unban player Doe (ucid=U1): no ban in scope"]


@pytest.mark.asyncio
async def test_an_unban_that_cleared_a_row_records_the_unban_in_the_audit_trail():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers, {"A": 1})
    interaction = _ReplyInteraction(_Member("Host A"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", "A")]
    assert plugin.audit_lines == ["unbanned player Doe (ucid=U1)"]


def _player_view(bus, servers, ucid="U1"):
    """The real player-view ``on_unban`` bound to a recorder instead of a live view."""
    module = sys.modules["plugins.mission.views"]
    view = types.SimpleNamespace(ucid=ucid, ephemeral=True, bus=bus,
                                 bot=types.SimpleNamespace(servers=servers, roles={"Admin": ADMIN_ROLES},
                                                           audit=lambda *a, **k: asyncio.sleep(0)),
                                 stopped=False)
    view.on_unban = types.MethodType(module.InfoView.on_unban, view)
    view.stop = lambda: setattr(view, "stopped", True)
    return view


@pytest.mark.asyncio
async def test_the_player_view_unban_clears_only_the_issuers_scope():
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    calls: list[tuple] = []

    async def unban(ucid, server_name=None):
        calls.append((ucid, server_name))
        return 1 if server_name == "A" else 0

    view = _player_view(types.SimpleNamespace(servers=servers, unban=unban), servers)
    interaction = _ReplyInteraction(_Member("Host A"))

    await view.on_unban(interaction)

    assert calls == [("U1", "A")]
    assert interaction.sent == ["User U1 unbanned on A."]


@pytest.mark.asyncio
async def test_the_player_view_unban_says_nothing_was_in_scope():
    servers = {"A": _Server("A", ["Host A"])}

    async def unban(ucid, server_name=None):
        return 0

    view = _player_view(types.SimpleNamespace(servers=servers, unban=unban), servers)
    interaction = _ReplyInteraction(_Member("Host A"))

    await view.on_unban(interaction)

    assert interaction.sent == ["User U1 was not banned on A."]


def _mission_unban_button(server_name):
    """The real ``on_interaction`` listener bound to a recorder, fed one ``unban_`` button."""
    module = _plugin_module("plugins.mission.commands")
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    calls: list[tuple] = []

    async def unban(ucid, server_name=None):
        calls.append((ucid, server_name))
        return 1

    mission = types.SimpleNamespace(bot=types.SimpleNamespace(servers=servers,
                                                              roles={"DCS Admin": ["DCS Admin"]}),
                                    bus=types.SimpleNamespace(unban=unban))
    mission.on_interaction = types.MethodType(module.Mission.on_interaction, mission)
    return module, mission, calls


async def _no_bans():
    return []


class _AdminChannel:
    """The slice of an admin channel the alert uses: it records the view it was handed."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, content=None, embed=None, view=None):
        self.sent.append({"content": content, "embed": embed, "view": view})


def _ban_reject_listener():
    """The real ``onBanReject`` bound to a recorder — the admin alert's button producer."""
    module = _plugin_module("plugins.mission.listener")
    listener = object.__new__(module.MissionEventListener)
    channel = _AdminChannel()
    listener.bot = types.SimpleNamespace(get_admin_channel=lambda _server: channel)
    listener.bus = types.SimpleNamespace(bans=_no_bans)
    listener.channel = channel
    return module, listener


def _reject_payload():
    return {"name": "Doe", "ucid": "U1", "ipaddr": "10.0.0.1", "reason": "cheating"}


@pytest.mark.asyncio
async def test_the_admin_alerts_unban_button_carries_the_server_it_was_raised_on():
    """The producer must emit the ``<ucid>|<server>`` the consumer parses, or the unban is bot-wide."""
    module, listener = _ban_reject_listener()

    await module.MissionEventListener.onBanReject(listener, _Server("SRS-1", ["Host A"]),
                                                 _reject_payload())

    ids = [item.custom_id for item in listener.channel.sent[-1]["view"].children]
    assert ids == ["unban_U1|SRS-1", "cancel"]


@pytest.mark.asyncio
async def test_pressing_the_alerts_unban_button_lifts_only_its_servers_scope(monkeypatch):
    """End to end: the button the alert builds, pressed, unbans its ``managed_by`` cluster only."""
    module, listener = _ban_reject_listener()
    await module.MissionEventListener.onBanReject(listener, _Server("A", ["Host A"]),
                                                 _reject_payload())
    custom_id = next(item.custom_id for item in listener.channel.sent[-1]["view"].children
                     if item.label == "Unban")

    cmd_module, mission, calls = _mission_unban_button("A")
    monkeypatch.setattr(cmd_module.utils, "check_roles", lambda *args, **kwargs: True)
    interaction = _ButtonInteraction(_Member("DCS Admin"), custom_id)
    interaction.response.edit_message = interaction._edit

    await mission.on_interaction(interaction)

    assert calls == [("U1", "A")]


class _ButtonInteraction(_ReplyInteraction):
    def __init__(self, user, custom_id):
        super().__init__(user)
        self.type = discord.InteractionType.component
        self.data = {"custom_id": custom_id}
        self.message = types.SimpleNamespace(add_reaction=self._react)
        self.reactions: list[str] = []

    async def _react(self, emoji):
        self.reactions.append(emoji)

    async def _edit(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_the_server_events_unban_clears_only_that_controls_rows(monkeypatch):
    """The button names the server it was raised on; its ``managed_by`` cluster is the scope."""
    module, mission, calls = _mission_unban_button("A")
    monkeypatch.setattr(module.utils, "check_roles", lambda *args, **kwargs: True)
    interaction = _ButtonInteraction(_Member("DCS Admin"), "unban_U1|A")
    interaction.response.edit_message = interaction._edit

    await mission.on_interaction(interaction)

    assert calls == [("U1", "A")]
    assert interaction.reactions == ["\u2705"]


@pytest.mark.asyncio
async def test_the_server_events_unban_on_an_untagged_server_clears_the_bot_wide_row(monkeypatch):
    module, mission, calls = _mission_unban_button("U")
    monkeypatch.setattr(module.utils, "check_roles", lambda *args, **kwargs: True)
    interaction = _ButtonInteraction(_Member("DCS Admin"), "unban_U1|U")
    interaction.response.edit_message = interaction._edit

    await mission.on_interaction(interaction)

    assert calls == [("U1", None)]


@pytest.mark.asyncio
async def test_an_unban_button_without_a_server_clears_the_bot_wide_row(monkeypatch):
    """A button posted before the id carried the server has no scope to narrow to."""
    module, mission, calls = _mission_unban_button("A")
    monkeypatch.setattr(module.utils, "check_roles", lambda *args, **kwargs: True)
    interaction = _ButtonInteraction(_Member("DCS Admin"), "unban_U1")
    interaction.response.edit_message = interaction._edit

    await mission.on_interaction(interaction)

    assert calls == [("U1", None)]


@pytest.mark.asyncio
async def test_clearing_punishment_points_unbans_on_the_commands_own_server(monkeypatch):
    """``/forgive`` runs on one server, so it clears that server's ``managed_by`` cluster only."""
    module = _plugin_module("plugins.punishment.commands")
    calls: list[tuple] = []

    async def unban(ucid, server_name=None):
        calls.append((ucid, server_name))
        return 1

    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    plugin = object.__new__(module.Punishment)
    plugin.bus = types.SimpleNamespace(servers=servers, unban=unban)
    plugin.bot = types.SimpleNamespace(get_server=lambda _interaction: servers["A"],
                                       audit=lambda *args, **kwargs: asyncio.sleep(0))

    class _Conn:
        async def execute(self, sql, params=None):
            return _Cursor()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    plugin.apool = types.SimpleNamespace(connection=lambda: _Conn())
    monkeypatch.setattr(module.utils, "yn_question", lambda *args, **kwargs: asyncio.sleep(0, True))

    interaction = _ReplyInteraction(_Member("DCS Admin"))

    async def followup(message, ephemeral=False):
        interaction.sent.append(message)

    interaction.followup = types.SimpleNamespace(send=followup)
    await module.Punishment.forgive.callback(plugin, interaction, "U1")

    assert calls == [("U1", "A")]


# ------------------------------------------------- the /dcs ban command's own write loop (issuer)
#
# The derivations above decide the SET; this section pins the command that turns the set into ROWS.
# ``plugins/admin/commands.py`` ``Admin.ban`` -> ``BanModal.on_submit`` derives
# ``targets = [server.name] if server else _ban_targets(interaction.user, self.bot.servers)``, then
# writes one row per target — or a single ``None`` row when the set is empty — all sharing ONE
# ``banned_at``, which is the key the bus groups them back into one ban with.


class _BanInteraction:
    """The slice of an interaction ``/dcs ban`` and its modal touch: the modal it opens and the reply."""

    def __init__(self, user):
        self.user = user
        self.channel = None
        self.client = types.SimpleNamespace(get_server=lambda _interaction: None)
        self.modal = None
        self.sent: list[str] = []
        self.response = types.SimpleNamespace(send_modal=self._send_modal, send_message=self._send,
                                              defer=self._defer)
        self.followup = types.SimpleNamespace(send=self._send, send_message=self._send)

    async def _send_modal(self, modal):
        self.modal = modal

    async def _defer(self, **kwargs):
        return None

    async def _send(self, message="", ephemeral=False):
        self.sent.append(message)


#: a UCID the command's ``user`` option may deliver: one the installation has never seen.
_BAN_UCID = "a" * 32


def _admin_ban(servers):
    """The real ``/dcs ban`` command and its ``BanModal`` bound to a recorder instead of a live cog."""
    module = sys.modules["plugins.admin.commands"]
    plugin = object.__new__(module.Admin)
    calls: list[dict] = []
    audits: list[str] = []

    async def ban(ucid, banned_by, reason="n/a", days=None, server_name=None, banned_at=None, until=None):
        calls.append({"ucid": ucid, "reason": reason, "days": days, "server_name": server_name,
                      "banned_at": banned_at})

    async def get_ucid_by_member(_member):
        return "ucid-1"

    async def get_member_or_name_by_ucid(_ucid):
        return "Doe"

    async def audit(message, *args, **kwargs):
        audits.append(message)

    plugin.bus = types.SimpleNamespace(ban=ban)
    plugin.bot = types.SimpleNamespace(servers=servers, audit=audit, roles={"Admin": ADMIN_ROLES},
                                       get_ucid_by_member=get_ucid_by_member,
                                       get_member_or_name_by_ucid=get_member_or_name_by_ucid)
    plugin.log = types.SimpleNamespace(exception=lambda *args, **kwargs: None)
    plugin.calls = calls
    plugin.audit_lines = audits
    return module, plugin, calls


async def _run_ban(module, plugin, interaction, server=None, reason="cheating", period=""):
    """Run ``/dcs ban`` for *interaction*'s user and submit the modal it opens."""
    await module.Admin.ban.callback(plugin, interaction, _BAN_UCID, server)
    modal = interaction.modal
    modal.reason = types.SimpleNamespace(value=reason)
    modal.period = types.SimpleNamespace(value=period)
    await modal.on_submit(interaction)


@pytest.mark.asyncio
async def test_the_ban_command_writes_one_row_per_managed_server_sharing_one_stamp():
    """No server named: one row per tagged server, ONE ``banned_at``, and the untagged one is not
    written (the issuer's set is the servers their roles tag, never every server)."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Host A"))

    await _run_ban(module, plugin, interaction, period="7")

    assert [c["server_name"] for c in calls] == ["A", "B"]
    assert len({id(c["banned_at"]) for c in calls}) == 1, "the rows of one ban share ONE stamp"
    assert calls[0]["banned_at"] is not None
    assert {c["ucid"] for c in calls} == {_BAN_UCID}
    assert {c["days"] for c in calls} == {7}
    assert {c["reason"] for c in calls} == {"cheating"}
    assert interaction.sent == ["Player Doe banned on A, B for 7 days."]


@pytest.mark.asyncio
async def test_the_ban_command_writes_a_single_bot_wide_row_when_the_issuer_manages_no_server():
    """No role of the issuer tags any server: ONE ``server_name=None`` row, stated as all servers."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Admin", "DCS Admin"))

    await _run_ban(module, plugin, interaction)

    assert [c["server_name"] for c in calls] == [None]
    assert interaction.sent == ["Player Doe banned on all servers."]
    assert plugin.audit_lines == [f'banned player Doe (ucid={_BAN_UCID} with reason "cheating" '
                                  'permanently. on all servers.']


@pytest.mark.asyncio
async def test_the_ban_command_writes_one_row_when_the_issuers_roles_tag_exactly_one_server():
    """One tagged server, so one row: the DERIVATION sets the count and the loop writes what it gets."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Host A"))

    await _run_ban(module, plugin, interaction)

    assert [c["server_name"] for c in calls] == ["A"]
    assert interaction.sent == ["Player Doe banned on A."]


@pytest.mark.asyncio
async def test_a_named_server_writes_that_one_row_instead_of_the_issuers_whole_set():
    """A named server replaces the issuer's set — an unscoped caller may name an untagged server."""
    servers = {"A": _Server("A", ["Host A"]), "U": _Server("U")}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Admin", "DCS Admin"))

    await _run_ban(module, plugin, interaction, server=servers["U"])

    assert [c["server_name"] for c in calls] == ["U"]
    assert interaction.sent == ["Player Doe banned on U."]


class _RecordingPool:
    """An in-memory stand-in for the bus's pool: the INSERT params become the rows ``bans()`` reads back."""

    def __init__(self):
        self.rows: list[dict] = []

    def connection(self):
        return _RecordingConn(self)


class _RecordingConn:
    INSERT = "INSERT INTO bans (ucid, banned_by, reason, banned_at, banned_until, server_name)"

    def __init__(self, pool):
        self.pool = pool

    def cursor(self, **kwargs):
        return self

    async def execute(self, sql, params=None):
        if self.INSERT in " ".join(sql.split()):
            ucid, banned_by, reason, banned_at, banned_until, server_name = params
            self.pool.rows.append({"ucid": ucid, "discord_id": -1, "name": "Doe", "banned_by": banned_by,
                                   "reason": reason, "banned_at": banned_at,
                                   "banned_until": banned_until, "server_name": server_name})
        return self

    async def fetchone(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for row in self.pool.rows:
                yield row
        return gen()


async def _ban_command_against_the_real_bus(servers, issuer):
    """Run ``/dcs ban`` (no server named) through the REAL ``ServiceBus.ban``/``bans``, rows included.

    The plugin's ``bus`` IS the real service, so what the command writes are INSERT statements, and
    what the list reads back is the grouping those rows produce — not the command's own word for it.
    """
    bus = object.__new__(ServiceBus)
    bus.apool = _RecordingPool()
    bus.send_to_node = lambda payload: asyncio.sleep(0)

    async def get_member_or_name_by_ucid(_ucid):
        return "Doe"

    async def audit(*args, **kwargs):
        return None

    async def send(*args, **kwargs):
        return None

    module = sys.modules["plugins.admin.commands"]
    plugin = object.__new__(module.Admin)
    plugin.bus = bus
    plugin.bot = types.SimpleNamespace(servers=servers, audit=audit, send=send, roles={"Admin": ADMIN_ROLES},
                                       get_member_or_name_by_ucid=get_member_or_name_by_ucid)
    plugin.log = types.SimpleNamespace(exception=lambda *args, **kwargs: None)
    interaction = _BanInteraction(issuer)

    await _run_ban(module, plugin, interaction)
    return bus, interaction


@pytest.mark.asyncio
async def test_the_rows_the_ban_command_writes_read_back_as_one_ban_of_the_issuers_servers():
    """The whole chain: /dcs ban -> one INSERT per managed server on ONE stamp -> ONE grouped entry."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host A", "Host B"]),
               "U": _Server("U")}
    bus, interaction = await _ban_command_against_the_real_bus(servers, _Member("Host A"))

    assert [(row["server_name"], row["ucid"], row["reason"]) for row in bus.apool.rows] == \
        [("A", _BAN_UCID, "cheating"), ("B", _BAN_UCID, "cheating")]
    assert bus.apool.rows[0]["banned_at"] == bus.apool.rows[1]["banned_at"]
    assert bus.apool.rows[0]["server_name"] != bus.apool.rows[1]["server_name"]

    entries = await bus.bans()

    assert [(entry["ucid"], entry["server_name"]) for entry in entries] == \
        [(_BAN_UCID, ["A", "B"])], "the N rows are ONE ban whose scope is the issuer's whole set"
    assert interaction.sent == ["Player Doe banned on A, B."]


@pytest.mark.asyncio
async def test_the_rows_an_unscoped_issuers_ban_writes_read_back_as_one_bot_wide_entry():
    """The same chain for a caller whose roles tag nothing: ONE ``server_name IS NULL`` row."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    bus, interaction = await _ban_command_against_the_real_bus(servers, _Member("Admin", "DCS Admin"))

    assert [(row["server_name"], row["ucid"]) for row in bus.apool.rows] == \
        [(None, _BAN_UCID)], "an unscoped issuer writes ONE row that names no server"

    entries = await bus.bans()

    assert [(entry["ucid"], entry["server_name"]) for entry in entries] == [(_BAN_UCID, None)]
    assert interaction.sent == ["Player Doe banned on all servers."]


# ----------------------------------------------------- the Admin door at every member-derived entry
# The issuer's set is derived by ONE rule (``core.utils.discord.member_ban_targets``). An Admin is
# unrestricted — bot-wide — even when their roles also tag a server; the Discord doors must say so.


@pytest.mark.asyncio
async def test_an_admin_whose_roles_tag_one_server_still_bans_bot_wide():
    """The reported symptom: an Admin's roles tag ONE server, yet the ban must be bot-wide."""
    servers = {"A": _Server("A", ["Admin", "Host A"]), "B": _Server("B", ["Host B"]), "U": _Server("U")}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Admin", "Host A"))

    await _run_ban(module, plugin, interaction)

    assert [c["server_name"] for c in calls] == [None]
    assert interaction.sent == ["Player Doe banned on all servers."]


@pytest.mark.asyncio
async def test_an_admin_naming_a_server_bans_on_that_server_only():
    """Naming a server replaces the issuer's whole set — for an Admin as for anyone."""
    servers = {"A": _Server("A", ["Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_ban(servers)
    interaction = _BanInteraction(_Member("Admin", "Host A"))

    await _run_ban(module, plugin, interaction, server=servers["B"])

    assert [c["server_name"] for c in calls] == ["B"]
    assert interaction.sent == ["Player Doe banned on B."]


@pytest.mark.asyncio
async def test_an_admin_unban_clears_every_row_even_when_their_roles_tag_one_server():
    servers = {"A": _Server("A", ["Admin", "Host A"]), "B": _Server("B", ["Host B"])}
    module, plugin, calls = _admin_unban(servers, {None: 2})
    interaction = _ReplyInteraction(_Member("Admin", "Host A"))

    await module.Admin.unban.callback(plugin, interaction, "U1")

    assert calls == [("U1", None)]
    assert interaction.sent == ["Player Doe unbanned on all servers."]


@pytest.mark.asyncio
async def test_the_player_view_unban_of_an_admin_clears_every_row():
    servers = {"A": _Server("A", ["Admin", "Host A"]), "B": _Server("B", ["Host B"])}
    calls: list[tuple] = []

    async def unban(ucid, server_name=None):
        calls.append((ucid, server_name))
        return 1

    view = _player_view(types.SimpleNamespace(servers=servers, unban=unban), servers)
    interaction = _ReplyInteraction(_Member("Admin", "Host A"))

    await view.on_unban(interaction)

    assert calls == [("U1", None)]
    assert interaction.sent == ["User U1 unbanned on all servers."]


@pytest.mark.asyncio
async def test_the_rows_an_admins_ban_writes_read_back_as_one_bot_wide_entry():
    """The whole chain for an Admin that also tags a server: ONE ``server_name IS NULL`` row."""
    servers = {"A": _Server("A", ["Admin", "Host A"]), "B": _Server("B", ["Host B"])}
    bus, interaction = await _ban_command_against_the_real_bus(servers, _Member("Admin", "Host A"))

    assert [(row["server_name"], row["ucid"]) for row in bus.apool.rows] == [(None, _BAN_UCID)]

    entries = await bus.bans()

    assert [(entry["ucid"], entry["server_name"]) for entry in entries] == [(_BAN_UCID, None)]
    assert interaction.sent == ["Player Doe banned on all servers."]
