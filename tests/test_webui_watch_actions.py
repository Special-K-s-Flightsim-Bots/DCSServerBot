"""The watchlist's two console writes — REMOVE and EDIT — on BOTH surfaces.

One section per acceptance criterion of the card:

* the pair's DECLARATION: one capability (``players.watch``) for both, one glyph each, the remove
  confirming through its dialog and the edit a form with ONE field (the reason, 80 characters);
* the ROUTES: both are POSTs carrying the one capability, their dialogs are POSTs too, and a caller
  without the capability is refused at the gate while the entry is untouched;
* the REMOVE: it deletes that UCID's row and audits it; a POST that skipped the dialog is refused;
  a replayed one-shot token deletes nothing;
* the EDIT: only ``reason`` changes — ``created_by`` and ``created_at`` come out byte-identical, an
  empty reason is refused with nothing written, and the dialog PRE-FILLS the current reason;
* the BOUND: a narrowed caller may only change a watch entry of a player it may see, the same
  presence rule the player read applies, and a foreign player answers the SAME sentence as a
  missing entry;
* the CONTROLS: ``watch_controls`` offers the pair on a watch entry and nothing at all on a player
  without one;
* the DISCORD path: the modal's update calls the SAME ``edit_watch`` action the console calls, and
  ``watch add`` still runs its own INSERT (the create path).

Driven through the REAL application (the production factory, the shell, the local login backend and
a stub source) with an in-memory watchlist behind the node's pool, and — for the Discord half —
through the modal's own ``on_submit`` with a recorded action seam. The harness is imported from the
write suites rather than copied.
"""
from __future__ import annotations

import asyncio
import datetime
import re
import types

import pytest
from starlette.requests import Request

from core.actions import TRANSPORT_DISCORD, TRANSPORT_WEB, ActionContext
from services.webservice import permissions, readmodels
from services.webservice.pages import actions as actions_page
from plugins.mission import actions as mission_actions
from plugins.mission import views as mission_views
from plugins.mission.actions import MAX_WATCH_REASON_LENGTH, edit_watch, unwatch_player
from test_webui_write_pause import (  # noqa: F401 - the fixtures are imported on purpose
    build, clean_registry, client, config_dir, fleet, open_page, sign_in, token_of,
)

REMOVE = actions_page.WATCH_REMOVE_PATH
EDIT = actions_page.WATCH_EDIT_PATH
ADD = actions_page.WATCH_ADD_PATH
CONFIRM = actions_page.CONFIRM_SUFFIX
ADD_DIALOG = ADD + CONFIRM
REMOVE_DIALOG = REMOVE + CONFIRM
EDIT_DIALOG = EDIT + CONFIRM

#: A REAL UCID: 32 characters, alphanumeric, lowercase (``core.utils.is_ucid``).
WATCHED = "4b1f9c22" * 4
UNWATCHED = "deadbeef" * 4
#: The fixture name the entry's own row carries.
NAME = "Test Pilot"
CREATED_AT = datetime.datetime(2026, 10, 1, 9, 30, 15)
REASON = "suspected aimbot"


# ------------------------------------------------------------------ the in-memory watchlist

class _Cursor:
    """A cursor answering ONE statement from the store and recording it."""

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

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """A connection handing out recording cursors and the ``conn.execute`` shortcut."""

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
    """An ``apool`` double over ONE watchlist table, one players table and one presence set.

    It answers by a MARKER only the intended statement carries, so a read the writer did not expect
    answers nothing and the test fails loudly instead of passing on a stray row.
    """

    def __init__(self, *, players=None, watch=None, present=None):
        self.players = {row["ucid"]: dict(row) for row in (players or ())}
        self.watch = {row["player_ucid"]: dict(row) for row in (watch or ())}
        self.present = set(present or ())
        self.statements: list[tuple[str, object]] = []

    # the pool the seam reads off ``ctx.node``
    def connection(self):
        return _Conn(self)

    def rows(self) -> list[dict]:
        return [dict(row) for row in self.watch.values()]

    def statements_matching(self, marker: str) -> list[tuple[str, object]]:
        return [(statement, params) for statement, params in self.statements
                if marker in statement]

    def run(self, statement: str, params):
        """The rows (and the affected-row count) *statement* answers against this store."""
        def value(key):
            return (params or {}).get(key)

        if "DELETE FROM watchlist" in statement:
            ucid = (params or ())[0]
            return [], 1 if self.watch.pop(ucid, None) is not None else 0
        if "UPDATE watchlist" in statement:
            reason, ucid = (params or ("", ""))[0], (params or ("", ""))[1]
            if ucid in self.watch:
                self.watch[ucid]["reason"] = reason
                return [], 1
            return [], 0
        if "INSERT INTO watchlist" in statement:
            ucid, reason, by = (params or (None, "", ""))
            if ucid in self.watch:
                # ``ON CONFLICT (player_ucid) DO NOTHING``: an existing entry is left untouched and
                # the affected-row count is 0.
                return [], 0
            self.watch[ucid] = {"player_ucid": ucid, "reason": reason, "created_by": by,
                                "created_at": CREATED_AT}
            return [], 1
        if "SELECT reason FROM watchlist" in statement:
            row = self.watch.get((params or (None,))[0])
            return ([{"reason": row["reason"]}] if row else []), -1
        if "SELECT player_ucid AS ucid, reason, created_by, created_at" in statement:
            wanted = value("ucids") or ()
            found = [row for ucid, row in self.watch.items() if ucid in wanted]
            return found[:1], -1
        if "AS present" in statement:
            wanted = value("ucids") or ()
            return [{"present": any(ucid in self.present for ucid in wanted)}], -1
        if "WHERE ucid = %(ucid)s" in statement:
            row = self.players.get(value("ucid"))
            return ([dict(row)] if row else []), -1
        if "WHERE discord_id = %(discord_id)s" in statement:
            link = value("discord_id")
            return [dict(row) for row in self.players.values() if row["discord_id"] == link], -1
        if "FROM statistics" in statement:
            return [], -1
        return [], -1


def player(ucid=WATCHED, *, name=NAME, discord_id=-1):
    """One ``players`` row as the reads select it."""
    return {"ucid": ucid, "name": name, "discord_id": discord_id, "manual": False,
            "first_seen": datetime.datetime(2024, 1, 1), "last_seen": datetime.datetime(2026, 10, 1),
            "vip": False}


def watched(ucid=WATCHED, *, reason=REASON, by="Host A"):
    """One ``watchlist`` row."""
    return {"player_ucid": ucid, "reason": reason, "created_by": by, "created_at": CREATED_AT}


class StubBus:
    """The ``ServiceBus`` the console resolves: the caller's cluster view, a node with the pool.

    ``_node()`` reads ``bus.node`` (the watch writes' pool hangs on it) and ``_ban_service`` answers
    its own read here, so a player's detail can be built without a second service.
    """

    def __init__(self, servers=(), store=None):
        self.servers = {name: entry for name, entry in servers}
        self.node = types.SimpleNamespace(apool=store, master=True)
        self.ban_calls: list[dict] = []

    async def bans(self, *, expired=False, servers=None):
        return []


@pytest.fixture
def store():
    return Store(players=[player(WATCHED), player(UNWATCHED)],
                 watch=[watched()], present={WATCHED, UNWATCHED})


@pytest.fixture
def watch_bus(store):
    """Install the stub bus under the REAL ``ServiceBus`` key (both lookups resolve it there)."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    saved = ServiceRegistry._singletons.get(ServiceBus)
    bus = StubBus(store=store)
    ServiceRegistry._singletons[ServiceBus] = bus
    try:
        yield bus
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


class AuditBot:
    """A bot whose ``audit`` records what it was asked to write, and which caches no member."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))

    async def get_members(self, user_ids, fetch=True):
        return {}


@pytest.fixture
def audit_bot():
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    bot = AuditBot()
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        yield bot
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


def notice(html: str) -> str:
    """The one-shot notice's message — the sentence a refused write left for the page it lands on."""
    match = re.search(r'<span class="notice-msg">(.*?)</span>', html, re.S)
    return match.group(1).strip() if match else ""


async def csrf(http) -> str:
    """The session's CSRF token, read out of the sign-in page's own form.

    The token belongs to the SESSION, so it is the one the write routes expect whatever page the
    person can actually open — a caller with no role and no scope is refused every list page, and a
    test must still be able to hand it a VALID token.
    """
    from services.webservice.auth import routes as auth_routes

    response = await http.get(auth_routes.LOGIN_PATH)
    return token_of(response.text)


async def remove(http, ucid=WATCHED, *, dialog=True):
    """Run the REMOVE: the dialog (which mints the token), then the dialog's own write."""
    token = await csrf(http)
    data = {"ucid": ucid, "_csrf_token": token}
    confirm = ""
    if dialog:
        reply = await http.post(REMOVE_DIALOG, data=data)
        assert reply.status_code == 200, reply.text
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', reply.text).group(1)
    payload = dict(data)
    if confirm:
        payload["_confirm_token"] = confirm
    return await http.post(REMOVE, data=payload)


async def edit(http, ucid=WATCHED, reason="cheating"):
    """Run the EDIT: the dialog (which pre-fills), then the form's own write."""
    token = await csrf(http)
    dialog = await http.post(EDIT_DIALOG, data={"ucid": ucid, "_csrf_token": token})
    assert dialog.status_code == 200, dialog.text
    return await http.post(EDIT, data={"ucid": ucid, "reason": reason, "_csrf_token": token})


# --------------------------------------------------------------------------- 1. the declarations

def test_one_capability_for_the_trio_and_the_declared_shape_of_each():
    """The card's declaration, read off the records: ONE capability, three glyphs, two forms."""
    assert actions_page.WATCH_CAPABILITY == "players.watch"
    for action in actions_page.WATCH_ACTIONS:
        assert action.capability == "players.watch"
    assert actions_page.WATCH_ADD_ACTION.qualname == "watch_player"
    assert actions_page.WATCH_REMOVE_ACTION.qualname == "unwatch_player"
    assert actions_page.WATCH_EDIT_ACTION.qualname == "edit_watch"
    assert actions_page.WATCH_ADD_ACTION.path == "/actions/watch/add"
    assert actions_page.WATCH_REMOVE_ACTION.path == "/actions/watch/remove"
    assert actions_page.WATCH_EDIT_ACTION.path == "/actions/watch/edit"
    assert actions_page.WATCH_REMOVE_ACTION.icon == "conceal"
    assert actions_page.WATCH_EDIT_ACTION.icon == "pencil"
    assert actions_page.WATCH_ADD_ACTION.icon == "warn"
    assert actions_page.WATCH_REMOVE_ACTION.confirm is True
    assert actions_page.WATCH_REMOVE_ACTION.danger is True
    assert actions_page.WATCH_REMOVE_ACTION.audit_refusals is True
    # the ADD is a FORM whose dialog is the write, signed with the actor's own name
    assert actions_page.WATCH_ADD_ACTION.confirm is False
    assert actions_page.WATCH_ADD_ACTION.form is True
    assert actions_page.WATCH_ADD_ACTION.danger is False
    assert actions_page.WATCH_ADD_ACTION.sender is True
    assert actions_page.WATCH_ADD_ACTION.field == "reason"
    assert actions_page.WATCH_ADD_ACTION.field_max == MAX_WATCH_REASON_LENGTH == 80
    assert actions_page.WATCH_ADD_ACTION.label == "Add to watchlist…"
    # the EDIT is a form whose ONE field is the reason, bounded by the Discord modal's limit
    assert actions_page.WATCH_EDIT_ACTION.confirm is False
    assert actions_page.WATCH_EDIT_ACTION.form is True
    assert actions_page.WATCH_EDIT_ACTION.field == "reason"
    assert actions_page.WATCH_EDIT_ACTION.field_max == MAX_WATCH_REASON_LENGTH == 80
    # the remove says what it costs: the reason, and that a ban is untouched
    assert "{player}" in actions_page.WATCH_REMOVE_ACTION.warning
    assert "not unbanned" in actions_page.WATCH_REMOVE_ACTION.warning
    assert "ban" in actions_page.WATCH_REMOVE_ACTION.detail
    # the ENTRY pair and the ADD are never offered together
    assert actions_page.WATCH_ENTRY_ACTIONS == (actions_page.WATCH_EDIT_ACTION,
                                                actions_page.WATCH_REMOVE_ACTION)
    assert actions_page.WATCH_ADD_ACTION not in actions_page.WATCH_ENTRY_ACTIONS
    assert actions_page.WATCH_ACTIONS == (actions_page.WATCH_ADD_ACTION,
                                          actions_page.WATCH_EDIT_ACTION,
                                          actions_page.WATCH_REMOVE_ACTION)


def test_both_glyphs_are_drawings_the_set_carries():
    """A name the glyph set does not carry renders an EMPTY ``<svg>`` — a blank button."""
    from pathlib import Path

    icons = (Path(actions_page.__file__).parent.parent / "templates" / "_icons.html").read_text(
        encoding="utf-8")
    for action in actions_page.WATCH_ACTIONS:
        assert f"'{action.icon}':" in icons, action.icon


def test_the_capability_is_declared_for_both_routes_and_their_dialogs():
    declared = actions_page.capabilities()
    assert declared[REMOVE] == "players.watch"
    assert declared[REMOVE_DIALOG] == "players.watch"
    assert declared[EDIT] == "players.watch"
    assert declared[EDIT_DIALOG] == "players.watch"
    # the same rule as the player row: the console's write roles, with a manager's scope grant
    assert permissions.roles_for_capability("players.watch") == ("Admin", "DCS Admin")
    assert permissions.scope_grants("players.watch") is True


# ------------------------------------------------------------------ 2. the remove, through the app

@pytest.mark.asyncio
async def test_removing_a_watch_deletes_the_row_and_leaves_an_audit_entry(
        config_dir, clean_registry, watch_bus, audit_bot):
    """Capability + the dialog's token → the row is gone and the attempt is in the trail."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await remove(http)
    assert reply.status_code == 303, (reply.status_code, reply.text[:200])
    assert watch_bus.node.apool.watch == {}, "the row must be deleted"
    assert len(audit_bot.calls) == 1, audit_bot.calls
    assert audit_bot.calls[0][1].get("actor") == "[web:local/specialk]", audit_bot.calls


@pytest.mark.asyncio
async def test_a_caller_without_the_capability_is_refused_and_the_row_stays(
        config_dir, clean_registry, watch_bus, audit_bot):
    """A valid CSRF token is not a substitute for ``players.watch``: 403 at the gate, nothing written."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "plain")
        token = await csrf(http)
        dialog = await http.post(REMOVE_DIALOG, data={"ucid": WATCHED, "_csrf_token": token})
        reply = await http.post(REMOVE, data={"ucid": WATCHED, "_csrf_token": token,
                                              "_confirm_token": "t" * 32})
    assert dialog.status_code == 403, dialog.status_code
    assert reply.status_code == 403, reply.status_code
    assert "Not authorized" in reply.json()["detail"]
    assert WATCHED in watch_bus.node.apool.watch, "the row must survive a refused caller"
    assert audit_bot.calls == []


@pytest.mark.asyncio
async def test_a_post_that_skipped_the_warning_is_refused(config_dir, clean_registry, watch_bus,
                                                          audit_bot):
    """The confirm step is server-enforced: no token, no removal — and the refusal is audited."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http)
        reply = await http.post(REMOVE, data={"ucid": WATCHED, "_csrf_token": token_of(page)})
    assert reply.status_code == 403, reply.status_code
    assert "confirmed through its dialog" in reply.json()["detail"]
    assert WATCHED in watch_bus.node.apool.watch
    assert len(audit_bot.calls) == 1, audit_bot.calls
    assert "not confirmed" in audit_bot.calls[0][0]


@pytest.mark.asyncio
async def test_a_replayed_token_removes_nothing(config_dir, clean_registry, watch_bus, audit_bot):
    """The token is SPENT on the way through: the same form posted twice cannot delete twice."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http)
        token = token_of(page)
        dialog = await http.post(REMOVE_DIALOG, data={"ucid": WATCHED, "_csrf_token": token})
        minted = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        first = await http.post(REMOVE, data={"ucid": WATCHED, "_csrf_token": token,
                                              "_confirm_token": minted})
        assert first.status_code == 303
        # the entry exists again: the replay the second POST would delete something
        watch_bus.node.apool.watch[WATCHED] = watched()
        replayed = await http.post(REMOVE, data={"ucid": WATCHED, "_csrf_token": token,
                                                 "_confirm_token": minted})
    assert replayed.status_code == 403, replayed.status_code
    assert WATCHED in watch_bus.node.apool.watch, "a spent token must delete nothing"


@pytest.mark.asyncio
async def test_a_manager_removes_a_watch_entry_of_a_player_on_their_own_server(
        config_dir, clean_registry, watch_bus, audit_bot):
    """The scope grant reaches a manager: a player seen on THEIR server is theirs to unwatch.

    The other half — a player outside the caller's set is refused with the missing-entry sentence and
    nothing written — is pinned at the action's own seam
    (``test_a_narrowed_caller_reaches_only_its_own_players``), because the dialog itself answers 404
    for a player the caller may not see, exactly like a UCID nothing answers to.
    """
    watch_bus.node.apool.present = {WATCHED}       # the player was seen on Host A's servers
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")              # a pure MANAGER of Host A
        reply = await remove(http)
    assert reply.status_code == 303, (reply.status_code, reply.text[:200])
    assert reply.headers["location"] == _players_path()
    assert watch_bus.node.apool.watch == {}, "a manager's own player must be removable"
    assert len(audit_bot.calls) == 1, audit_bot.calls
    assert audit_bot.calls[0][1].get("actor") == "[web:local/hoster]", audit_bot.calls


@pytest.mark.asyncio
async def test_a_dialog_for_a_player_the_caller_may_not_see_is_the_bare_404(
        config_dir, clean_registry, watch_bus, audit_bot):
    """No entry and an out-of-scope entry answer the SAME 404, so the door discloses nothing."""
    watch_bus.node.apool.present = set()
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        token = await csrf(http)
        watched = await http.post(REMOVE_DIALOG, data={"ucid": WATCHED, "_csrf_token": token})
        missing = await http.post(REMOVE_DIALOG, data={"ucid": "0" * 32, "_csrf_token": token})
    assert watched.status_code == missing.status_code == 404
    assert WATCHED in watch_bus.node.apool.watch


# -------------------------------------------------------------------- 3. the edit, through the app

@pytest.mark.asyncio
async def test_editing_rewrites_the_reason_and_never_re_stamps_the_history(
        config_dir, clean_registry, watch_bus, audit_bot):
    """``created_by``/``created_at`` come out BYTE-identical; only the reason changes."""
    before = watched()
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, reason="known cheat")
    row = watch_bus.node.apool.watch[WATCHED]
    assert reply.status_code == 303, (reply.status_code, reply.text[:200])
    assert row["reason"] == "known cheat"
    assert row["created_by"] == before["created_by"]
    assert row["created_at"] == before["created_at"]
    # the write names the reason column ALONE, and the reason travels as a bound parameter
    written = watch_bus.node.apool.statements_matching("UPDATE watchlist")
    assert len(written) == 1, written
    statement, params = written[0]
    assert statement == "UPDATE watchlist SET reason = %s WHERE player_ucid = %s"
    assert params == ("known cheat", WATCHED)
    assert len(audit_bot.calls) == 1, audit_bot.calls


@pytest.mark.asyncio
async def test_an_empty_reason_is_refused_and_writes_nothing(config_dir, clean_registry,
                                                             watch_bus, audit_bot):
    """An empty reason is a REMOVAL, not an edit — the sentence comes back and nothing is written.

    The players page renders no one-shot banner yet, so the refusal is read off the AUDIT trail and
    the untouched row rather than off markup.
    """
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await edit(http, reason="   ")
    assert reply.status_code == 303, reply.status_code
    assert watch_bus.node.apool.statements_matching("UPDATE watchlist") == []
    assert watch_bus.node.apool.watch[WATCHED]["reason"] == REASON
    assert len(audit_bot.calls) == 1, audit_bot.calls
    assert "empty reason" in audit_bot.calls[0][0], audit_bot.calls


@pytest.mark.asyncio
async def test_the_edit_dialog_pre_fills_the_entry_and_offers_one_field(
        config_dir, clean_registry, watch_bus, audit_bot):
    """The form IS the write, so it carries the CURRENT reason and nothing else."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http)
        dialog = await http.post(EDIT_DIALOG, data={"ucid": WATCHED,
                                                   "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    assert REASON in dialog.text, "the reason must be pre-filled"
    assert 'name="reason"' in dialog.text
    assert 'name="until"' not in dialog.text, "a watch entry has no expiry"
    assert 'name="_confirm_token"' not in dialog.text, "an edit mints no token"


# ---------------------------------------------------------------- 2b. the add, on both surfaces

@pytest.mark.asyncio
async def test_adding_a_watch_inserts_the_row_once(audit_bot):
    """The CREATE: one INSERT with its conflict clause, a refusal the second time, nothing rewritten."""
    store = Store(players=[player(UNWATCHED)], present={UNWATCHED})
    result = await mission_actions.watch_player(context(store), UNWATCHED, "testing the reason",
                                                sender="Host A")
    assert result.success is True, result.message
    assert store.watch[UNWATCHED]["reason"] == "testing the reason"
    assert store.watch[UNWATCHED]["created_by"] == "Host A"
    written = store.statements_matching("INSERT INTO watchlist")
    assert len(written) == 1, written
    statement, params = written[0]
    assert "ON CONFLICT (player_ucid) DO NOTHING" in statement, \
        "the duplicate must be answered by the conflict clause, not a read-then-write race"
    assert params == (UNWATCHED, "testing the reason", "Host A")
    # a second add of the same player is refused and leaves the entry alone
    again = await mission_actions.watch_player(context(store), UNWATCHED, "other", sender="x")
    assert again.success is False
    assert "already on the watchlist" in again.message
    assert store.watch[UNWATCHED]["reason"] == "testing the reason"


@pytest.mark.asyncio
async def test_adding_a_watch_refuses_an_empty_reason(audit_bot):
    store = Store(players=[player(UNWATCHED)], present={UNWATCHED})
    result = await mission_actions.watch_player(context(store), UNWATCHED, "  ")
    assert result.success is False
    assert "reason is required" in result.message
    assert store.statements_matching("INSERT INTO watchlist") == []


@pytest.mark.asyncio
async def test_the_discord_create_path_calls_the_add_action_and_writes_no_sql(monkeypatch):
    """``/mission watch add`` opens the modal, and the modal's submit calls ``watch_player``.

    The modal holds no SQL of its own: the create has ONE write path, shared with the console.
    """
    calls: list[tuple] = []

    async def fake_call_action(qualname, ctx, /, **params):
        calls.append((qualname, params))
        return types.SimpleNamespace(success=True, message="ok")

    monkeypatch.setattr(mission_views, "call_action", fake_call_action)
    monkeypatch.setattr(mission_views, "ActionContext",
                        types.SimpleNamespace(from_interaction=lambda interaction: "ctx"))
    modal = mission_views.WatchModal(WATCHED)
    assert modal.reason.default is None
    modal.reason._value = "suspected aimbot"       # the operator types it
    sent = {}

    async def send_message(content, ephemeral=False):
        sent["message"] = content

    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(display_name="tester"),
        client=types.SimpleNamespace(get_server=lambda interaction: None),
        response=types.SimpleNamespace(send_message=send_message))
    await modal.on_submit(interaction)
    assert calls == [("watch_player", {"ucid": WATCHED, "reason": "suspected aimbot"})]
    assert actions_page.WATCH_ADD_ACTION.qualname == "watch_player"
    assert sent["message"] == "ok"


def test_the_modal_source_holds_no_sql():
    """The create's INSERT lives in the ACTION — the modal only forwards the reason."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "plugins" / "mission" / "views.py").read_text(
        encoding="utf-8")
    assert "INSERT INTO watchlist" not in source
    assert "UniqueViolation" not in source


@pytest.mark.asyncio
async def test_adding_a_watch_through_the_console_writes_the_entry(
        config_dir, clean_registry, watch_bus, audit_bot):
    """The dialog opens for a player with NO entry, and its own POST writes the row."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = await csrf(http)
        dialog = await http.post(ADD_DIALOG, data={"ucid": UNWATCHED, "_csrf_token": token})
        assert dialog.status_code == 200, dialog.text
        reply = await http.post(ADD, data={"ucid": UNWATCHED, "reason": "metagaming",
                                           "_csrf_token": token})
    assert reply.status_code == 303, (reply.status_code, reply.text[:200])
    row = watch_bus.node.apool.watch[UNWATCHED]
    assert row["reason"] == "metagaming"
    assert row["created_by"] == "specialk", "the actor's shown name, off the identity"
    assert len(audit_bot.calls) == 1, audit_bot.calls


@pytest.mark.asyncio
async def test_the_add_dialog_is_the_404_for_a_watched_player(config_dir, clean_registry,
                                                              watch_bus, audit_bot):
    """The ADD is the control of the entry-less state, so its door answers nothing for an entry."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = await csrf(http)
        watched = await http.post(ADD_DIALOG, data={"ucid": WATCHED, "_csrf_token": token})
        unknown = await http.post(ADD_DIALOG, data={"ucid": "0" * 32, "_csrf_token": token})
    assert watched.status_code == unknown.status_code == 404


def test_the_add_control_is_offered_only_where_there_is_no_entry(monkeypatch):
    """One control of the capability per state: ADD when entry-less, the pair when watched."""
    _register_actions()
    monkeypatch.setattr(permissions, "role_names_for", lambda request: frozenset({"Admin"}))
    monkeypatch.setattr(permissions, "manages_console", lambda request: False)
    request = _request()
    assert actions_page.watch_add_control(request, _detail(active=True), "/players") is None
    control = actions_page.watch_add_control(request, _detail(active=False), "/players")
    assert control is not None
    assert control["path"] == ADD_DIALOG
    assert control["icon"] == "warn"
    assert control["hidden"].count(("ucid", WATCHED)) == 1


# ------------------------------------------------------------------------- 4. the built controls

def _request(app=None) -> Request:
    """A bare request over this app, with an empty session the CSRF field can mint into."""
    return Request({"type": "http", "method": "GET", "path": "/players", "headers": [],
                    "session": {},
                    "app": app or types.SimpleNamespace(state=types.SimpleNamespace())})


def _detail(*, active: bool, ucid: str = WATCHED):
    """The player read as the controls consume it — a live entry, or none at all.

    The read model derives ``watch.active`` from the entry's PRESENCE, so "no entry" is ``None`` here.
    """
    entry = {"reason": REASON, "created_by": "Host A", "created_at": "2026-10-01 09:30 UTC",
             "ucid": ucid}
    return readmodels.player_detail({"ucid": ucid, "found": True,
                                     "identity": [{"ucid": ucid, "name": NAME, "manual": False}],
                                     "name": NAME,
                                     "watch": entry if active else None})


def _register_actions() -> None:
    """Populate the process action registry the way the shell does.

    ``conftest`` clears ``core.actions._ACTIONS`` around EVERY test (it is process state), so a test
    that reads ``action_available`` has to trigger the discovery itself — re-importing the module is
    what runs the ``@action`` decorators.
    """
    import sys

    from core.actions import discover_actions

    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])


def test_the_pair_is_offered_on_a_watch_entry_and_absent_without_one(monkeypatch):
    """An unusable control is ABSENT: no entry → no strip; and the order is the mockup's."""
    _register_actions()
    monkeypatch.setattr(permissions, "role_names_for", lambda request: frozenset({"Admin"}))
    monkeypatch.setattr(permissions, "manages_console", lambda request: False)
    request = _request()
    assert actions_page.watch_controls(request, _detail(active=False), "/players") is None
    strip = actions_page.watch_controls(request, _detail(active=True), "/players")
    assert strip is not None and strip["wide"] is False
    controls = strip["strip"]
    assert [control["path"] for control in controls] == [EDIT_DIALOG, REMOVE_DIALOG]
    assert [control["icon"] for control in controls] == ["pencil", "conceal"]
    assert all(control["hidden"].count(("ucid", WATCHED)) == 1 for control in controls)


def test_the_pair_is_absent_without_the_capability_or_the_action(monkeypatch):
    """Nothing is drawn for a caller who may not write, and nothing for an absent action."""
    monkeypatch.setattr(permissions, "role_names_for", lambda request: frozenset({"DCS"}))
    monkeypatch.setattr(permissions, "manages_console", lambda request: False)
    assert actions_page.watch_controls(_request(), _detail(active=True), "/players") is None
    monkeypatch.setattr(permissions, "role_names_for", lambda request: frozenset({"Admin"}))
    monkeypatch.setattr(actions_page, "action_available", lambda qualname: False)
    assert actions_page.watch_controls(_request(), _detail(active=True), "/players") is None


# ---------------------------------------------------------------- 5. the actions, at their own seam

def context(store, *, scope=None, servers=None, transport=TRANSPORT_WEB) -> ActionContext:
    """An action context over the store's pool, with the caller's scope as the seam reads it."""
    ctx = ActionContext(node=types.SimpleNamespace(apool=store),
                        bus=types.SimpleNamespace(servers=servers or {}))
    ctx._transport = transport
    ctx._scope = scope
    ctx._audit_user = "tester"
    return ctx


class Scope:
    """A scope double: ``unrestricted`` plus the ``allows(managed_by)`` predicate."""

    def __init__(self, *, unrestricted=False, managed=("Host A",)):
        self.unrestricted = unrestricted
        self.managed = managed

    def allows(self, managed_by):
        return any(role in self.managed for role in (managed_by or ()))


def fleet_servers(*pairs):
    return {name: types.SimpleNamespace(name=name, locals={"managed_by": list(tags)})
            for name, tags in pairs}


@pytest.mark.asyncio
async def test_the_actions_run_on_the_discord_transport_too(audit_bot):
    """The Discord command calls these SAME functions, so neither may be console-only."""
    store = Store(players=[player(WATCHED)], watch=[watched()], present={WATCHED})
    result = await unwatch_player(context(store, transport=TRANSPORT_DISCORD), WATCHED)
    assert result.success is True, result.message
    assert store.watch == {}
    store.watch[WATCHED] = watched()
    result = await edit_watch(context(store, transport=TRANSPORT_DISCORD), WATCHED, "cheat")
    assert result.success is True, result.message
    assert store.watch[WATCHED]["reason"] == "cheat"


@pytest.mark.asyncio
async def test_a_missing_entry_is_refused_and_writes_nothing(audit_bot):
    """The DELETE finds no row (its own affected-row count) and nothing is written or claimed."""
    store = Store(players=[player(UNWATCHED)], watch=[], present={UNWATCHED})
    result = await unwatch_player(context(store), UNWATCHED)
    assert result.success is False
    assert "No watch entry" in result.message
    assert store.watch == {}, "a refusal leaves the watchlist exactly as it was"
    result = await edit_watch(context(store), UNWATCHED, "cheat")
    assert result.success is False
    assert "No watch entry" in result.message
    assert store.statements_matching("UPDATE watchlist") != []
    assert store.watch == {}


@pytest.mark.asyncio
async def test_an_over_long_reason_is_refused_and_writes_nothing(audit_bot):
    store = Store(players=[player(WATCHED)], watch=[watched()], present={WATCHED})
    result = await edit_watch(context(store), WATCHED, "x" * (MAX_WATCH_REASON_LENGTH + 1))
    assert result.success is False
    assert str(MAX_WATCH_REASON_LENGTH) in result.message
    assert store.statements_matching("UPDATE watchlist") == []


@pytest.mark.asyncio
async def test_a_narrowed_caller_reaches_only_its_own_players(audit_bot):
    """The bound is the player read's own presence rule, applied inside the action."""
    store = Store(players=[player(WATCHED)], watch=[watched()], present=set())
    narrow = context(store, scope=Scope(managed=("Host A",)),
                     servers=fleet_servers(("SRS-1", ("Host A",))))
    result = await unwatch_player(narrow, WATCHED)
    assert result.success is False
    assert "No watch entry" in result.message
    assert WATCHED in store.watch
    store.present = {WATCHED}
    result = await unwatch_player(narrow, WATCHED)
    assert result.success is True, result.message
    assert store.watch == {}


# ------------------------------------------------------------------- 6. the Discord command path

def test_the_command_offers_edit_beside_add_remove_and_list():
    """``/mission watch edit`` exists, gated like its siblings.

    Read off the module's own SOURCE: ``plugins/mission/commands.py`` imports ``openpyxl`` through the
    bot's library closure, which a headless test machine need not have, so the cog cannot be imported
    here. The pin is the declaration, not the wording: the ``watch`` group carries a subcommand named
    ``edit`` whose body opens the modal with an EXISTING reason, and it carries the same role gate the
    remove half does (``app_has_role('DCS Admin')``).
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "plugins" / "mission"
              / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    group_names = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   for target in node.targets
                   if isinstance(target, ast.Name)
                   and isinstance(node.value, ast.Call)
                   and getattr(node.value.func, "id", "") == "Group"}
    assert "watch" in group_names, "the watch group must be declared"
    edits = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any(isinstance(decorator, ast.Call)
                     and getattr(decorator.func, "attr", "") == "command"
                     and any(keyword.arg == "name"
                             and getattr(keyword.value, "value", "") == "edit"
                             for keyword in decorator.keywords)
                     for decorator in node.decorator_list)]
    assert len(edits) == 1, "exactly one watch edit subcommand"
    body = ast.dump(edits[0])
    assert "WatchModal" in body and "existing" in body, "the edit must pre-fill the modal"
    gates = [ast.dump(decorator) for decorator in edits[0].decorator_list]
    assert any("app_has_role" in gate for gate in gates), \
        "the edit carries the same role gate as its siblings"


def test_the_modal_pre_fills_and_calls_the_same_action(monkeypatch):
    """The update path calls ``edit_watch`` — the SAME qualname the console's Edit declares."""
    calls: list[tuple] = []

    async def fake_call_action(qualname, ctx, /, **params):
        calls.append((qualname, params))
        return types.SimpleNamespace(success=True, message="ok")

    monkeypatch.setattr(mission_views, "call_action", fake_call_action)
    monkeypatch.setattr(mission_views, "ActionContext",
                        types.SimpleNamespace(from_interaction=lambda interaction: "ctx"))
    monkeypatch.setattr(mission_views.ServiceRegistry, "get",
                        staticmethod(lambda key: types.SimpleNamespace(bot=None)))
    modal = mission_views.WatchModal(WATCHED, existing=REASON)
    assert modal.reason.default == REASON, "the field must be pre-filled"
    # what the operator SUBMITS is the component's own value (a modal-field default is a proposal)
    modal.reason._value = REASON
    message = {}

    async def send_message(content, ephemeral=False):
        message["content"] = content

    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(display_name="tester"),
        client=types.SimpleNamespace(get_server=lambda interaction: None),
        response=types.SimpleNamespace(send_message=send_message))
    asyncio.run(modal.on_submit(interaction))
    assert calls == [("edit_watch", {"ucid": WATCHED, "reason": REASON})]
    assert actions_page.WATCH_EDIT_ACTION.qualname == "edit_watch"
    assert message["content"] == "ok"


def _players_path() -> str:
    from services.webservice.pages import players as players_page

    return players_page.PLAYERS_PATH


# --------------------------------------------------- 7. the Discord REMOVE goes through the action

def _watch_command_source():
    """The ``watch`` group's ``delete`` subcommand, parsed out of the cog's source.

    ``plugins/mission/commands.py`` imports ``aiofiles``/``openpyxl`` at module level, which a
    headless test machine need not have, so the cog cannot be imported here. The pin is therefore the
    declaration and its body, read as SYNTAX — never a wording of the reply.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "plugins" / "mission"
              / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == "delete"
             and any(isinstance(decorator, ast.Call)
                     and getattr(decorator.func, "attr", "") == "command"
                     and getattr(decorator.func.value, "id", "") == "watch"
                     for decorator in node.decorator_list)]
    assert len(found) == 1, "exactly one watch remove subcommand"
    return found[0]


@pytest.mark.asyncio
async def test_the_discord_remove_dispatches_the_unwatch_action(monkeypatch):
    """The remove asks the ACTION — the qualname the console's Remove declares — and prints its reply."""
    calls: list[tuple] = []

    async def fake_call_action(qualname, ctx, /, **params):
        calls.append((qualname, params))
        return types.SimpleNamespace(success=True, message="Watch entry for X removed.")

    monkeypatch.setattr(mission_views, "call_action", fake_call_action)
    monkeypatch.setattr(mission_views, "ActionContext",
                        types.SimpleNamespace(from_interaction=lambda interaction: "ctx"))
    message = await mission_views.remove_watch_entry(types.SimpleNamespace(), WATCHED)
    assert calls == [("unwatch_player", {"ucid": WATCHED})], calls
    assert message == "Watch entry for X removed."
    assert actions_page.WATCH_REMOVE_ACTION.qualname == "unwatch_player"


@pytest.mark.asyncio
async def test_the_discord_remove_reports_the_actions_refusal_not_a_success(monkeypatch):
    """A UCID with no entry answers the ACTION's refusal — the command invents no success sentence."""
    async def fake_call_action(qualname, ctx, /, **params):
        return types.SimpleNamespace(success=False, message="No watch entry for X.")

    monkeypatch.setattr(mission_views, "call_action", fake_call_action)
    monkeypatch.setattr(mission_views, "ActionContext",
                        types.SimpleNamespace(from_interaction=lambda interaction: "ctx"))
    message = await mission_views.remove_watch_entry(types.SimpleNamespace(), UNWATCHED)
    assert message == "No watch entry for X."
    assert "removed from the watchlist" not in message.lower()


def test_the_remove_command_replies_with_the_dispatched_sentence_and_builds_no_sql():
    """The subcommand hands the UCID to the shared dispatch and sends ITS result — no write of its own."""
    import ast

    node = _watch_command_source()
    called = [call for call in ast.walk(node)
              if isinstance(call, ast.Call)
              and getattr(call.func, "id", "") == "remove_watch_entry"]
    assert len(called) == 1, "the remove must dispatch through the shared unwatch helper"
    assert [ast.dump(argument) for argument in called[0].args][-1] == ast.dump(ast.Name(id="ucid",
                                                                                       ctx=ast.Load()))
    body = ast.dump(node)
    assert "DELETE FROM watchlist" not in body
    assert "execute" not in body, "the command opens no connection of its own"


def test_the_remove_subcommand_sends_the_actions_sentence_as_the_reply():
    """The reply content IS the dispatch's result: one call whose value reaches ``send_message``."""
    import ast

    node = _watch_command_source()

    def dispatched(expression) -> bool:
        if isinstance(expression, ast.Await):
            expression = expression.value
        return (isinstance(expression, ast.Call)
                and getattr(expression.func, "id", "") == "remove_watch_entry")

    assignments = [item for item in node.body if isinstance(item, ast.Assign)
                   and dispatched(item.value)]
    assert len(assignments) == 1, ast.dump(node)
    sent = [call for item in node.body if isinstance(item, ast.Expr)
            for call in ast.walk(item)
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "send_message"]
    assert len(sent) == 1, "the remove answers once"
    assert ast.dump(sent[0].args[0]) == ast.dump(
        ast.Name(id=assignments[0].targets[0].id, ctx=ast.Load()))


def test_the_remove_subcommand_keeps_its_role_gate():
    """The role gate the siblings carry is untouched: a caller without it never reaches the action."""
    import ast

    node = _watch_command_source()
    gates = [ast.dump(decorator) for decorator in node.decorator_list]
    assert any("app_has_role" in gate and "DCS Admin" in gate for gate in gates), gates
    assert any("guild_only" in gate for gate in gates), gates


def test_the_watchlist_row_is_deleted_from_the_action_alone():
    """Exactly ONE ``DELETE FROM watchlist`` in the mission plugin, and it is the action's own.

    The FILE is what matters, never the line number: a pin on the line rots with every edit above it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "plugins" / "mission"
    hits = sorted((path.name, number)
                  for path in root.rglob("*.py")
                  for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                  if "DELETE FROM watchlist" in line)
    assert [name for name, _ in hits] == ["actions.py"], hits
    for name in ("commands.py", "views.py"):
        assert not any(hit[0] == name for hit in hits), f"{name} must build no SQL"


def test_the_player_view_unwatch_dispatches_the_same_action():
    """The profile view's Unwatch button is the SAME write: no second delete lives in a view."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "plugins" / "mission"
              / "views.py").read_text(encoding="utf-8")
    assert "remove_watch_entry(interaction, self.ucid)" in source
    assert "DELETE FROM watchlist" not in source
