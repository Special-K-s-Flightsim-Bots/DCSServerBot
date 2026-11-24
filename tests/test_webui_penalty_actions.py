"""The player page's penalty-point card and its three writes.

One section per acceptance criterion:

* the DECLARATION: one capability (``players.punish``), three controls, two of them pointing at the
  same action (a positive figure adds, a negative one removes), the forgive confirming, and NO
  server field anywhere on the surface;
* the ONE WRITE PATH: ``writes.record_event`` is one INSERT plus one publish, and the ``/punish``
  command calls it and holds neither the SQL nor a publish of its own;
* the ACTIONS at their own seam: a write resolves its FLEET REFERENCE itself (a server hosted by
  this process's node, else the first by name across the bus), an installation with no server is
  refused, add writes the row and publishes once, remove writes a negative row, forgive deletes
  every UCID of the link and lifts the bot's ban once per UCID with no server, and each refusal
  (capability, no server at all, an absurd figure) writes nothing;
* a FAILED publish is reported as a partial outcome, never as a clean success;
* the PAGE: the three controls are present for a ``players.punish`` holder and absent otherwise, and
  the card shows the read's own ``penalty_total``;
* the DIALOGS carry the points and the reason (the forgive the consequences and the token) and NO
  server field, all rendered through the real application with no Discord connection at all.
"""
from __future__ import annotations

import ast
import contextlib
import types
from pathlib import Path

import pytest

from core.actions import ActionContext, TRANSPORT_DISCORD, TRANSPORT_PLUGIN, TRANSPORT_WEB
from services.webservice.pages import actions as actions_page
from services.webservice.pages import player_detail as page
from plugins.punishment import actions as penalty_actions
from plugins.punishment import writes
from plugins.punishment.actions import forgive_player, punish_player
from test_webui_player_detail import detail_data, infractions_data, statistics_data
from test_webui_write_pause import (  # noqa: F401 - the console's harness
    build, clean_registry, client, config_dir, fleet, open_page, sign_in, token_of,
)

REPO = Path(__file__).resolve().parent.parent

ADD = actions_page.PENALTY_ADD_PATH
REMOVE = actions_page.PENALTY_REMOVE_PATH
FORGIVE = actions_page.PENALTY_FORGIVE_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX
UCID = "4b1f9c22" * 4
LINKED = "deadbeef" * 4
SERVER = "SRS-1"


# --------------------------------------------------------------------------- the doubles

class _Cursor:
    def __init__(self, store):
        self._store = store
        self._rows: list[dict] = []
        self._iterator = iter(())
        self.rowcount = -1

    async def execute(self, statement, params=None):
        self._rows, self.rowcount = self._store.run(statement, params)
        self._store.statements.append((statement, params))
        self._iterator = iter(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return _Cursor(self._store)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    async def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


class _Conn:
    def __init__(self, store):
        self._store = store

    def cursor(self, *args, **kwargs):
        return _Cursor(self._store)

    async def execute(self, statement, params=None):
        cursor = _Cursor(self._store)
        await cursor.execute(statement, params)
        return cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Store:
    """An ``apool`` double over the penalty log and the players' Discord link."""

    def __init__(self, *, rows=None, players=None):
        self.rows: list[tuple[str, str, str, float]] = list(rows or ())
        self.players = dict(players or {UCID: -1})
        self.statements: list[tuple[str, object]] = []

    def connection(self):
        return _Conn(self)

    def run(self, statement: str, params):
        if "INSERT INTO pu_events" in statement:
            ucid, server_name, event, points = params
            self.rows.append((ucid, server_name, event, points))
            return [], 1
        if "DELETE FROM pu_events" in statement:
            ucid = params[0]
            before = len(self.rows)
            self.rows = [row for row in self.rows if row[0] != ucid]
            return [], before - len(self.rows)
        if "SELECT ucid, discord_id FROM players" in statement:
            ucid = params[0]
            if ucid not in self.players:
                return [], -1
            return [{"ucid": ucid, "discord_id": self.players[ucid]}], -1
        if "SELECT ucid FROM players WHERE discord_id" in statement:
            link = params[0]
            return [{"ucid": name} for name, discord in self.players.items()
                    if discord == link], -1
        return [], -1

    def inserted(self) -> list[tuple]:
        return [(statement, params) for statement, params in self.statements
                if "INSERT INTO pu_events" in statement]


class Publisher:
    def __init__(self, *, fail: bool = False):
        self.payloads: list[dict] = []
        self.fail = fail

    async def __call__(self, payload):
        if self.fail:
            raise RuntimeError("bus down")
        self.payloads.append(payload)


class AuditBot:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.cogs: dict = {}

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))

    def get_cog(self, name):
        return self.cogs.get(name) if name == "Punishment" else None


@pytest.fixture
def bot_stub():
    """The ``BotService`` the action resolves: one bot carrying the punishment cog and an audit."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    publisher = Publisher()
    bot = AuditBot()
    bot.cogs["Punishment"] = types.SimpleNamespace(
        trigger=types.SimpleNamespace(publish=publisher))
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        yield bot, publisher
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


class Scope:
    """A scope double: ``unrestricted`` plus ``allows(managed_by)``."""

    def __init__(self, *, unrestricted=False, managed=("Host A",)):
        self.unrestricted = unrestricted
        self.resolved = True
        self.managed = managed

    def allows(self, managed_by):
        return any(role in self.managed for role in (managed_by or ()))


def server(name: str, *, remote: bool = False, managed=("Host A",)):
    """One server double: its name, whether another node hosts it, and its ``managed_by``."""
    return types.SimpleNamespace(name=name, is_remote=remote, locals={"managed_by": list(managed)})


def servers_fixture():
    """The fleet: ONE server on this process's node (``SRS-1``) and two on other nodes."""
    return {
        "SRS-1": server("SRS-1", remote=False),
        "SRS-2": server("SRS-2", remote=True),
        "SRS-B": server("SRS-B", remote=True, managed=("Host B",)),
    }


class Fleet:
    """A ``ServiceBus`` double: the servers the fleet carries and every unban it was asked to lift."""

    def __init__(self, servers=None):
        self.servers = servers_fixture() if servers is None else dict(servers)
        self.unbanned: list[tuple] = []

    async def unban(self, ucid, server_name=None):
        self.unbanned.append((ucid, server_name))
        return 1


@contextlib.contextmanager
def installed(service):
    """Install *service* as the ``ServiceBus`` the actions resolve, and restore the previous one."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    saved = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = service
    try:
        yield service
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


@pytest.fixture
def fleet_bus():
    """The process's fleet: ``SRS-1`` on this node, ``SRS-2`` and ``SRS-B`` on others."""
    with installed(Fleet()) as service:
        yield service


def context(store, *, roles=("DCS Admin",), scope: object = ..., servers=None,
            transport=TRANSPORT_WEB):
    ctx = ActionContext(node=types.SimpleNamespace(apool=store, guild_id=1),
                        bus=types.SimpleNamespace(servers=servers or {}))
    ctx._transport = transport
    ctx._roles = frozenset(roles)
    ctx._scope = Scope(unrestricted=True) if scope is ... else scope
    ctx._audit_user = "tester"
    return ctx


class BanService:
    """A ``ServiceBus`` double whose ``unban`` records every UCID it was asked to lift."""

    def __init__(self):
        self.unbanned: list[tuple] = []

    async def unban(self, ucid, server_name=None):
        self.unbanned.append((ucid, server_name))
        return 1


# ----------------------------------------------------------------------- 1. the declaration

def test_one_capability_and_the_declared_shape_of_each_control():
    assert actions_page.PENALTY_CAPABILITY == "players.punish"
    assert [action.key for action in actions_page.PENALTY_ACTIONS] == [
        "add_points", "remove_points", "forgive"]
    assert actions_page.PENALTY_ADD_ACTION.qualname == "punish_player"
    assert actions_page.PENALTY_REMOVE_ACTION.qualname == "punish_player"
    assert actions_page.PENALTY_FORGIVE_ACTION.qualname == "forgive_player"
    assert actions_page.PENALTY_ADD_ACTION.points is True
    assert actions_page.PENALTY_REMOVE_ACTION.points is True
    assert actions_page.PENALTY_FORGIVE_ACTION.points is False
    assert actions_page.PENALTY_FORGIVE_ACTION.confirm is True
    assert actions_page.PENALTY_FORGIVE_ACTION.danger is True
    # every control of this surface is reached through its dialog
    assert all(action.dialog for action in actions_page.PENALTY_ACTIONS)


def test_the_surface_carries_no_server_field_at_all():
    """The write is fleet-wide: the console declares no server field and no picker helper."""
    assert not hasattr(actions_page, "PENALTY_SERVER_FIELD")
    for name in ("penalty_servers", "penalty_server_options", "player_last_server"):
        assert not hasattr(actions_page, name), name
    for action in actions_page.PENALTY_ACTIONS:
        fields = actions_page.penalty_inputs(action)
        assert "server" not in {field["name"] for field in fields}, action.key


def test_the_capability_is_declared_for_both_posts_of_every_control():
    declared = actions_page.capabilities()
    for action in actions_page.PENALTY_ACTIONS:
        assert declared[action.path] == "players.punish"
        assert declared[action.path + CONFIRM] == "players.punish"
    assert actions_page.permissions.roles_for_capability("players.punish") == ("Admin", "DCS Admin")
    assert actions_page.permissions.scope_grants("players.punish") is True


# ------------------------------------------------------------------- 2. the one write path

@pytest.mark.asyncio
async def test_record_event_is_one_insert_and_one_publish():
    """The extracted pair: the command's INSERT and its publish payload, exactly once each."""
    store = Store()
    publisher = Publisher()
    await writes.record_event(store, publisher, ucid=UCID, server_name=SERVER, event="teamkill",
                              points=3, guild_id=4711)
    inserted = store.inserted()
    assert len(inserted) == 1, inserted
    assert inserted[0][1] == (UCID, SERVER, "teamkill", 3)
    assert len(publisher.payloads) == 1
    payload = publisher.payloads[0]
    assert payload["node"] == "Master"
    assert payload["guild_id"] == 4711
    data = payload["data"].obj if hasattr(payload["data"], "obj") else payload["data"]
    assert data == {"init_id": UCID, "server_name": SERVER, "event": "teamkill", "points": 3}


def test_the_punish_command_calls_the_one_write_helper():
    """``/punish`` holds neither the SQL nor a publish: it hands both to the helper, once."""
    source = (REPO / "plugins" / "punishment" / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "record_event"]
    assert len(calls) == 1, "the command must call the shared write helper exactly once"
    assert "INSERT INTO pu_events" not in source
    assert ".publish({" not in source
    assert "from .writes import _ban_targets, record_event" in source


# --------------------------------------------------------------- 3. the actions at their seam

@pytest.mark.asyncio
async def test_adding_points_writes_the_row_and_publishes_once(bot_stub, fleet_bus):
    bot, publisher = bot_stub
    store = Store()
    result = await punish_player(context(store), UCID, 5, "teamkill")
    assert result.success is True, result.message
    assert store.rows == [(UCID, SERVER, "teamkill", 5)]
    assert len(publisher.payloads) == 1
    assert len(bot.calls) == 1
    assert "changed punishment points" in bot.calls[0][0]


@pytest.mark.asyncio
async def test_an_empty_reason_is_the_admin_event_and_removing_writes_a_negative_row(
        bot_stub, fleet_bus):
    store = Store()
    await punish_player(context(store), UCID, 2, "")
    await punish_player(context(store), UCID, -3, "correction")
    assert store.rows == [(UCID, SERVER, "admin", 2), (UCID, SERVER, "correction", -3)]


# ------------------------------------------------------- 3a. the fleet reference, in its order

@pytest.mark.asyncio
async def test_the_reference_is_a_server_on_this_processs_node(bot_stub):
    """A LOCAL server wins over a remote one that sorts earlier by name."""
    fleet_ = Fleet({"AAA-remote": server("AAA-remote", remote=True),
                    "ZZZ-local": server("ZZZ-local", remote=False)})
    store = Store()
    with installed(fleet_):
        result = await punish_player(context(store), UCID, 5, "teamkill")
    assert result.success is True, result.message
    assert store.rows == [(UCID, "ZZZ-local", "teamkill", 5)]


@pytest.mark.asyncio
async def test_the_reference_falls_back_to_the_first_server_by_name_across_the_bus(bot_stub):
    """With nothing hosted here, the FIRST server by name across the bus is the reference."""
    fleet_ = Fleet({"BBB-remote": server("BBB-remote", remote=True),
                    "AAA-remote": server("AAA-remote", remote=True)})
    store = Store()
    with installed(fleet_):
        result = await punish_player(context(store), UCID, 5, "teamkill")
    assert result.success is True, result.message
    assert store.rows == [(UCID, "AAA-remote", "teamkill", 5)]


@pytest.mark.asyncio
async def test_the_reference_ignores_the_callers_scope_and_the_callers_bus(bot_stub):
    """The reference is an installation fact: a caller scoped to nothing still writes, and the
    server named on the row comes from the fleet — never from the caller's own view."""
    store = Store()
    ctx = context(store, scope=Scope(managed=("Host C",)),
                  servers={"ONLY-IN-MY-VIEW": server("ONLY-IN-MY-VIEW", remote=True)})
    with installed(Fleet()):
        result = await punish_player(ctx, UCID, 5, "teamkill")
    assert result.success is True, result.message
    assert store.rows == [(UCID, SERVER, "teamkill", 5)]


@pytest.mark.asyncio
async def test_a_fleet_with_no_server_is_refused_and_audited(bot_stub):
    """No server at all: one sentence, one audit entry, and nothing written."""
    bot, publisher = bot_stub
    store = Store()
    with installed(Fleet({})):
        result = await punish_player(context(store), UCID, 5, "teamkill")
    assert result.success is False
    assert result.message == penalty_actions.NO_FLEET_REFUSAL
    assert store.rows == [] and publisher.payloads == []
    assert len(bot.calls) == 1, "the refusal is audited"
    assert penalty_actions.NO_FLEET_REFUSAL in bot.calls[0][0]


@pytest.mark.asyncio
async def test_forgive_refuses_a_fleet_with_no_server_too(bot_stub):
    store = Store(rows=[(UCID, SERVER, "teamkill", 3)])
    with installed(Fleet({})):
        result = await forgive_player(context(store), UCID)
    assert result.success is False
    assert result.message == penalty_actions.NO_FLEET_REFUSAL
    assert store.rows == [(UCID, SERVER, "teamkill", 3)], "nothing is deleted"


# -------------------------------------------------------- 3b. forgive: bot-wide, once per UCID

@pytest.mark.asyncio
async def test_forgive_deletes_every_row_of_the_link_and_lifts_the_bots_ban_bot_wide(bot_stub):
    """Every UCID of the link loses its rows, and each is unbanned ONCE with no server — the
    bot-wide row, exactly what ``/forgive`` does when its server declares no ``managed_by``."""
    store = Store(rows=[(UCID, SERVER, "a", 1), (LINKED, SERVER, "b", 2)],
                  players={UCID: 4711, LINKED: 4711, "0" * 32: 4711})
    service = Fleet()
    with installed(service):
        result = await forgive_player(context(store), UCID)
    assert result.success is True, result.message
    assert store.rows == [], "every UCID of the link must lose its rows"
    assert service.unbanned == [(UCID, None), (LINKED, None), ("0" * 32, None)], \
        "one bot-wide unban per UCID of the group, never a per-server fan-out"


@pytest.mark.asyncio
async def test_forgive_calls_unban_once_with_none_for_an_unlinked_player(bot_stub):
    """An unlinked player is its own group: exactly ONE unban, and its server argument is ``None``."""
    store = Store(rows=[(UCID, SERVER, "teamkill", 3)])
    service = Fleet()
    with installed(service):
        result = await forgive_player(context(store), UCID)
    assert result.success is True, result.message
    assert service.unbanned == [(UCID, None)]


# ---------------------------------------------------------------- 3c. the refusals write nothing

@pytest.mark.asyncio
async def test_the_capability_refusal_writes_nothing(bot_stub, fleet_bus):
    bot, publisher = bot_stub
    store = Store()
    result = await punish_player(context(store, roles=("DCS",), scope=None), UCID, 5, "x")
    assert result.success is False
    assert result.message == penalty_actions.CAPABILITY_REFUSAL
    assert store.rows == [] and publisher.payloads == []


@pytest.mark.parametrize("points", [0, 1001, -1001])
@pytest.mark.asyncio
async def test_an_absurd_or_zero_figure_is_refused_by_the_action(bot_stub, fleet_bus, points):
    store = Store()
    result = await punish_player(context(store), UCID, points, "x")
    assert result.success is False
    assert store.rows == []


@pytest.mark.asyncio
async def test_a_failed_publish_is_reported_honestly(bot_stub, fleet_bus):
    bot, publisher = bot_stub
    publisher.fail = True
    store = Store()
    result = await punish_player(context(store), UCID, 5, "x")
    assert result.success is False
    assert "recorded" in result.message
    assert result.data.get("points_written") is True
    assert store.rows == [(UCID, SERVER, "x", 5)], "the row is written before the publish"


# ------------------------------------------------------- 3d. the transport guard, both actions

@pytest.mark.asyncio
async def test_punish_player_refuses_a_caller_that_is_not_the_website(bot_stub, fleet_bus):
    """``TRANSPORT_WEB`` is the only admitted caller, and the refusal leaves every effect untouched:
    no ``pu_events`` statement is prepared, no publish is made, no ban is lifted, nothing is
    audited."""
    bot, publisher = bot_stub
    store = Store()
    service = Fleet()
    with installed(service):
        result = await punish_player(context(store, transport=TRANSPORT_DISCORD), UCID, 5,
                                     "teamkill")
    assert result.success is False
    assert result.message == penalty_actions.CONSOLE_ONLY
    assert store.statements == [], "a refused transport must not reach the database"
    assert store.rows == []
    assert publisher.payloads == [], "nothing may be published for a refused caller"
    assert service.unbanned == [], "no ban may be lifted for a refused caller"
    assert bot.calls == [], "the guard answers before the audit, so no entry is written for it"


@pytest.mark.asyncio
async def test_forgive_player_refuses_a_caller_that_is_not_the_website(bot_stub, fleet_bus):
    """Same guard on the delete: the rows survive, nothing is deleted, nothing is published and no
    ban is lifted."""
    bot, publisher = bot_stub
    store = Store(rows=[(UCID, SERVER, "teamkill", 3), (LINKED, SERVER, "teamkill", 3)],
                  players={UCID: 4711, LINKED: 4711})
    service = Fleet()
    with installed(service):
        result = await forgive_player(context(store, transport=TRANSPORT_PLUGIN), UCID)
    assert result.success is False
    assert result.message == penalty_actions.CONSOLE_ONLY
    assert store.statements == [], "a refused transport must not reach the database"
    assert store.rows == [(UCID, SERVER, "teamkill", 3), (LINKED, SERVER, "teamkill", 3)]
    assert publisher.payloads == [], "nothing may be published for a refused caller"
    assert service.unbanned == [], "no ban may be lifted for a refused caller"


# --------------------------------------------------------------------------------- 4. the page

def _stub_reads(monkeypatch, ucid=UCID):
    """The page's and the dialog's read seam: one player, one infractions answer, statistics."""
    calls: list[tuple[str, dict]] = []

    async def read(qualname, ctx, /, **params):
        calls.append((qualname, dict(params)))
        if qualname == "get_player_detail":
            return types.SimpleNamespace(success=True, data=detail_data(ucid, name="Dirk"))
        if qualname == "get_player_infractions":
            return types.SimpleNamespace(success=True, data=infractions_data(ucid, penalty_total=9.5))
        if qualname == "get_player_statistics":
            return types.SimpleNamespace(success=True, data=dict(statistics_data(ucid),
                                                                 period="all"))
        raise AssertionError(qualname)

    monkeypatch.setattr(page, "read_action", read)
    monkeypatch.setattr(actions_page, "read_action", read)
    return calls


def test_the_three_controls_are_offered_to_a_players_punish_holder(config_dir, clean_registry,
                                                                   monkeypatch):
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            return await open_page(http, f"/players/{UCID}/infractions")

    html = asyncio.run(run())
    assert f'action="{ADD}{CONFIRM}"' in html
    assert f'action="{REMOVE}{CONFIRM}"' in html
    assert f'action="{FORGIVE}{CONFIRM}"' in html
    # the card states the scope of the controls: the whole fleet, not one server
    assert "change the points of the whole fleet, not of one server" in html
    # the card shows the read's own total
    assert "9.5" in html


def test_the_controls_are_absent_without_the_capability(config_dir, clean_registry, monkeypatch):
    _stub_reads(monkeypatch)
    from services.webservice import permissions as permissions_module

    allows = permissions_module.allows
    monkeypatch.setattr(permissions_module, "allows",
                        lambda capability, roles, *, manager=False:
                        False if capability == "players.punish"
                        else allows(capability, roles, manager=manager))
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            return await open_page(http, f"/players/{UCID}/infractions")

    html = asyncio.run(run())
    assert actions_page.PENALTY_ADD_PATH not in html
    assert actions_page.PENALTY_FORGIVE_PATH not in html


def _dialogs(config_dir, clean_registry, monkeypatch, user="mod1"):
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, user)
            token = token_of(await open_page(http, f"/players/{UCID}/infractions"))
            replies = {}
            for path in (ADD, REMOVE, FORGIVE):
                replies[path] = await http.post(path + CONFIRM,
                                                data={"ucid": UCID, "_csrf_token": token})
            return replies

    return asyncio.run(run())


def test_no_dialog_carries_a_server_field(config_dir, clean_registry, monkeypatch):
    """All three dialogs render, and none of them asks for a server: the write is fleet-wide."""
    replies = _dialogs(config_dir, clean_registry, monkeypatch)
    assert len(replies) == 3
    for path, reply in replies.items():
        assert reply.status_code == 200, (path, reply.text[:300])
        assert 'name="server"' not in reply.text, path
        assert "<select" not in reply.text, path
        assert "SRS-1" not in reply.text, path


def test_the_point_dialogs_ask_for_the_points_and_the_reason(config_dir, clean_registry,
                                                             monkeypatch):
    replies = _dialogs(config_dir, clean_registry, monkeypatch)
    for path in (ADD, REMOVE):
        text = replies[path].text
        assert 'name="points"' in text, path
        assert 'name="reason"' in text, path
        assert 'name="_confirm_token"' not in text, "a point change mints no token"


def test_the_forgive_dialog_states_both_consequences_and_carries_the_token(
        config_dir, clean_registry, monkeypatch):
    reply = _dialogs(config_dir, clean_registry, monkeypatch)[FORGIVE]
    text = reply.text
    assert "deleted" in text and "lifted" in text
    assert 'name="_confirm_token"' in text
    assert 'name="points"' not in text


# ------------------------------------------------------- 5. the route, end to end

@pytest.fixture
def app_bus():
    """The ``ServiceBus`` the app's ``_node()`` resolves: the store's pool and the fleet's servers."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    store = Store()
    servers = servers_fixture()

    class Service:
        def __init__(self):
            self.node = types.SimpleNamespace(apool=store, guild_id=1, master=True)
            self.servers = servers

        async def unban(self, ucid, server_name):
            return 1

    saved = ServiceRegistry._singletons.get(ServiceBus)
    service = Service()
    ServiceRegistry._singletons[ServiceBus] = service
    try:
        yield store
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


def test_the_add_route_writes_the_row_and_returns_to_the_infractions_tab(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            token = token_of(await open_page(http, f"/players/{UCID}/infractions"))
            return await http.post(ADD, data={"ucid": UCID, "points": "7",
                                              "reason": "teamkill", "_csrf_token": token})

    reply = asyncio.run(run())
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert reply.headers["location"] == f"/players/{UCID}/infractions"
    assert app_bus.rows == [(UCID, SERVER, "teamkill", 7)]


def test_a_posted_server_field_is_ignored_by_the_route(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    """Nothing reads a posted ``server``: the row names the fleet reference, not the posted name."""
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            token = token_of(await open_page(http, f"/players/{UCID}/infractions"))
            return await http.post(ADD, data={"ucid": UCID, "server": "SRS-B", "points": "7",
                                              "reason": "teamkill", "_csrf_token": token})

    reply = asyncio.run(run())
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert app_bus.rows == [(UCID, SERVER, "teamkill", 7)], \
        "the posted server must not decide the row's server"


def test_a_forgive_post_that_skipped_its_dialog_is_refused(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    app = build(config_dir, fleet(), plugins=("mission", "punishment"))
    app_bus.rows.append((UCID, SERVER, "teamkill", 3))

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            token = token_of(await open_page(http, f"/players/{UCID}/infractions"))
            return await http.post(FORGIVE, data={"ucid": UCID, "_csrf_token": token})

    reply = asyncio.run(run())
    assert reply.status_code == 403, reply.status_code
    assert app_bus.rows == [(UCID, SERVER, "teamkill", 3)]


# ------------------------------------------- 5b. the two point routes' distinct contracts

#: The ONE sentence a magnitude outside 1…1000 is refused with, read from the ACTION that composes
#: it — the console carries no wording of its own for this refusal.
POINTS_REFUSAL = penalty_actions.POINTS_REFUSAL.format(minimum=penalty_actions.POINTS_MIN,
                                                       maximum=penalty_actions.POINTS_MAX)


def _penalty_app(config_dir, monkeypatch):
    """The real application, the punishment actions discovered, the page's reads stubbed."""
    _stub_reads(monkeypatch)
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.punishment.actions", None)
    discover_actions(["punishment"])
    return build(config_dir, fleet(), plugins=("mission", "punishment"))


async def _post_points(http, path, points, reason="teamkill"):
    """POST one point control the way its dialog does: the session's token from a rendered form."""
    token = token_of(await open_page(http, f"/players/{UCID}/infractions"))
    return await http.post(path, data={"ucid": UCID, "points": points,
                                       "reason": reason, "_csrf_token": token})


def _run_points(app, path, points, *, reason="teamkill"):
    """One POST, then the page it lands on — the (reply, landed) pair."""
    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            reply = await _post_points(http, path, points, reason)
            landed = await http.get(reply.headers.get("location", _penalty_back(UCID)))
            return reply, landed

    return asyncio.run(run())


def _penalty_back(ucid):
    return f"/players/{ucid}/infractions"


@pytest.mark.parametrize("points", ["0", "-5", "1001", "abc"])
def test_the_add_route_refuses_a_figure_it_cannot_add(config_dir, clean_registry, monkeypatch,
                                                      app_bus, bot_stub, points):
    """A typed figure outside 1…1000 — a minus included — is refused by the ADD route, with the
    action's one sentence, and the pool records no statement at all."""
    app = _penalty_app(config_dir, monkeypatch)
    reply, landed = _run_points(app, ADD, points)
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert POINTS_REFUSAL in landed.text, (points, landed.text[:2000])
    assert app_bus.statements == [], f"'{points}' must not reach the database"
    assert app_bus.rows == []


@pytest.mark.parametrize("points", ["0", "1001", "abc"])
def test_the_remove_route_refuses_the_same_figures_with_the_same_sentence(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub, points):
    """The bound is one rule for both controls, so the refusal is one sentence for both."""
    app = _penalty_app(config_dir, monkeypatch)
    reply, landed = _run_points(app, REMOVE, points)
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert POINTS_REFUSAL in landed.text, (points, landed.text[:2000])
    assert app_bus.statements == [], f"'{points}' must not reach the database"


def test_the_remove_route_takes_a_positive_magnitude_and_writes_the_negative_row(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    """The two contracts stay distinct: a positive magnitude on Remove is the negative row."""
    app = _penalty_app(config_dir, monkeypatch)
    reply, _landed = _run_points(app, REMOVE, "4")
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert app_bus.rows == [(UCID, SERVER, "teamkill", -4)]


def test_the_add_route_writes_the_positive_row_for_the_same_figure(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    """The control for the test above: the same figure on Add is written with its own sign."""
    app = _penalty_app(config_dir, monkeypatch)
    reply, _landed = _run_points(app, ADD, "4")
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert app_bus.rows == [(UCID, SERVER, "teamkill", 4)]


# ------------------------------------------------------ 5c. the success notice, on its page

def test_the_success_notice_is_on_the_page_it_lands_on_and_only_once(
        config_dir, clean_registry, monkeypatch, app_bus, bot_stub):
    """POST → 303 → the landing page carries the action's own message; the next render does not."""
    app = _penalty_app(config_dir, monkeypatch)
    expected = penalty_actions._("User punished with {} points.").format(7)

    import asyncio

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            reply = await _post_points(http, ADD, "7")
            landed = await http.get(reply.headers["location"])
            again = await http.get(reply.headers["location"])
            return reply, landed, again

    reply, landed, again = asyncio.run(run())
    assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    assert reply.headers["location"] == _penalty_back(UCID)
    assert app_bus.rows == [(UCID, SERVER, "teamkill", 7)], "the write itself still lands"
    assert expected in landed.text, "the notice is missing from the page the write returns to"
    assert expected not in again.text, "a one-shot notice is shown exactly once"


# ---------------------------------------------------- 6. the two catalogs' three labels

#: The three labels this page puts on screen in each of the two draft catalogs.
TRANSLATED_LABELS = {
    "cn": {"Penalty points": "罚分", "Points": "分数", "Total": "总计"},
    "ru": {"Penalty points": "Штрафные очки", "Points": "Очки", "Total": "Всего"},
}


def test_the_three_penalty_labels_are_translated_in_cn_and_ru():
    """The labels answer from the catalog, in BOTH the source ``.po`` and the compiled ``.mo`` the
    console actually loads — an empty ``msgstr`` would fall back to English on screen."""
    from services.webservice import i18n

    for language, entries in TRANSLATED_LABELS.items():
        catalog = i18n.translations_for(language)
        source = (REPO / "locale" / language / "LC_MESSAGES" / "webui.po").read_text(encoding="utf-8")
        for msgid, translated in entries.items():
            assert f'msgid "{msgid}"\nmsgstr "{translated}"\n' in source, (language, msgid)
            assert catalog.gettext(msgid) == translated, (language, msgid)
