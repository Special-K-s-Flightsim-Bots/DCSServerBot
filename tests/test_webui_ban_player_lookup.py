"""The ADD ban dialog's UCID autocomplete: a name in, a UCID out — and never a player out of scope.

One section per acceptance criterion:

* the ADD's own player SEARCH is a declared GET, on the ADD's own capability, and a caller without it
  is refused;
* an UNSCOPED caller (an Admin) searches every player — the ``/info`` list, whole — while a NARROWED
  caller (a ``managed_by`` manager, and a ``DCS Admin`` whose cluster scope narrows them) reaches only
  the players PRESENT on the servers of its own view: a player on somebody else's server is simply not
  offered, and the bound the search was handed is exactly that presence set;
* an EMPTY or whitespace-only query answers NOTHING — never a fleet-wide dump;
* a name matching nobody is an empty list with HTTP 200;
* the LIMITATION is stated where it belongs: a narrowed caller cannot autocomplete a player who is not
  online on its servers, and TYPING that player's exact UCID still bans them (the no-JS path, and the
  write's own ``utils.is_ucid`` re-validation);
* the UCID field's declaration carries the ``data-lookup`` the enhancement reads, NO other dialog field
  carries one, and the dialog page loads the shipped script;
* the reused spelling (``utils.get_all_players``) bounds by the presence set as a WHERE term.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source) with the ban list behind the stub ServiceBus of the bans suite and the ``players`` table
behind a stub of the reused search, exactly like the suites this file extends; the shipped script
itself is run against a minimal DOM by ``player_lookup_harness.js``.
"""
from __future__ import annotations

import json
import re
import shutil
import types
from pathlib import Path

import pytest

import core.utils as core_utils
from core.actions import ActionContext
from plugins.mission.actions import BANS_CONSOLE_ONLY, _present_ucids, search_players
from services.webservice.pages import actions as actions_page
from test_webui_bans_add import (  # noqa: F401 - the ADD harness, reused on purpose
    ADD, ADD_DIALOG, UCID, add, written,
)
from test_webui_bans_page import (  # noqa: F401 - fixtures reused on purpose
    _transport_context, ban_bus, bans_page_html, _console_registrar,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures reused on purpose
    Cluster, build, clean_registry, client, config_dir, instance, server, sign_in, token_of,
)

REPO = Path(__file__).resolve().parent.parent
LOOKUP = actions_page.BAN_PLAYERS_PATH
QUERY = actions_page.BAN_PLAYERS_QUERY

#: ONE player per case, all named after the same word so a single query matches all of them:
#: ``HOME`` is online on the server the manager of ``Host A`` runs, ``AWAY`` on somebody else's server,
#: and ``OFFLINE`` is known to the table but sits on NO server right now.
HOME = "a" * 32
AWAY = "b" * 32
OFFLINE = "c" * 32
#: A second player online on the manager's OWN server whose name matches nothing the tests search
#: for: the presence bound carries it (the bound is the servers' presence, not the matches) while the
#: answer does not — which is what tells a WHERE bound apart from a post-filter of the rows.
BENCH = "d" * 32
#: the ``players`` table as the reused search reads it: ``(ucid, name, last_seen)``, most recent last.
PLAYERS = ((HOME, "Viper", 3), (AWAY, "Viper Two", 2), (OFFLINE, "Viper Three", 1),
           (BENCH, "Benchwarmer", 0))

#: the process's own node, read off the ServiceBus by ``context_for``: it is what the reused search is
#: HANDED, so a test can prove the action reads the context and not something of its own.
NODE = types.SimpleNamespace(name="dcs-a-01", pool="the-node")


class FakePlayers:
    """``utils.get_all_players`` as the search relies on it: the matched rows, the presence bound, and
    the 25-row cap, most recently seen first.

    The SQL itself (the ``= ANY(%s)`` term, the ORDER/LIMIT) is exercised against a live PostgreSQL by
    the probe recorded in the task workspace and pinned at the helper's own seam below (``StubPool``);
    this double is the DOMAIN boundary the console reads through, so the wiring under test here is the
    action's: WHICH bound it hands the search, and what it does with the rows.
    """

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls: list[types.SimpleNamespace] = []
        self.fail = False

    def __call__(self, node, linked=None, watchlist=None, vip=None, search=None, present=None):
        self.calls.append(types.SimpleNamespace(node=node, search=search,
                                               present=None if present is None else list(present)))
        if self.fail:
            raise RuntimeError("the players table is unreachable")
        term = (search or "").lower()
        found = [row for row in self.rows if term in (row[1] or "").lower() or term in row[0]]
        if present is not None:
            allowed = set(present)
            found = [row for row in found if row[0] in allowed]
        found.sort(key=lambda row: row[2], reverse=True)
        return [(row[0], row[1]) for row in found[:25]]


@pytest.fixture
def players_table(monkeypatch, ban_bus):
    """The reused search, stubbed, with the ban bus carrying the process's own node."""
    table = FakePlayers(PLAYERS)
    ban_bus.node = NODE
    monkeypatch.setattr(core_utils, "get_all_players", table)
    return table


def online(server_obj, *players):
    """Put *players* (ucid, name) in a server double's ``players`` map, active, as the bot holds them."""
    server_obj.players = {ucid: types.SimpleNamespace(ucid=ucid, name=name, active=True)
                          for ucid, name in players}
    return server_obj


def hosted_fleet() -> Cluster:
    """``SRS-1`` (Host A) with ``HOME`` and ``BENCH`` online, ``SRS-B`` (Host B) with ``AWAY`` online."""
    a = online(server("SRS-1", ["Host A"]), (HOME, "Viper"), (BENCH, "Benchwarmer"))
    b = online(server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance"),
               (AWAY, "Viper Two"))
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


async def lookup(http, query):
    """One GET of the ADD's own player search."""
    return await http.get(LOOKUP, params={QUERY: query})


def ucids(response) -> list[str]:
    return [row["ucid"] for row in response.json()["players"]]


# ───────────────────────────────────────────────────── 1. the route and its declaration

def test_the_search_is_a_declared_get_on_the_adds_own_capability():
    """A GET (it changes nothing), declared with ``players.ban`` — the door to the write it feeds."""
    registrar = _console_registrar()
    routes = [route for route in registrar.routes if route.path == LOOKUP]
    assert routes, "the ADD's player search is not registered"
    assert all(set(route.methods) == {"GET"} for route in routes), \
        "the search is reachable by a method it does not implement"
    assert registrar.capability_for(route=routes[0]) == actions_page.BAN_ADD_CAPABILITY
    assert actions_page.capabilities()[LOOKUP] == actions_page.BAN_ADD_CAPABILITY
    # the field's own URL IS the route: one string, so the declaration cannot point at a path nobody
    # serves (the dialog would then fetch a 404 on every keystroke).
    assert actions_page.BAN_ADD_UCID_LOOKUP == LOOKUP


@pytest.mark.asyncio
@pytest.mark.parametrize("who, status, expected", [
    ("specialk", 200, [HOME, AWAY, OFFLINE]),      # Admin: unscoped, the whole table
    ("mod1", 200, [HOME]),                         # DCS Admin: its cluster scope narrows to Host A
    ("hoster", 200, [HOME]),                       # a pure MANAGER of Host A
    ("hoster-b", 200, [AWAY]),                     # a pure MANAGER of Host B
    ("plain", 403, []),                            # no ``players.ban`` at all
])
async def test_the_identity_matrix_of_the_search(config_dir, clean_registry, ban_bus, players_table,
                                                 who, status, expected):
    """Every identity on one surface: what the search gives it, and what it is refused."""
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, who)
        response = await lookup(http, "viper")
    assert response.status_code == status, f"{who}: {response.text}"
    if status == 200:
        assert ucids(response) == expected, f"{who}: the search answered the wrong set"
        assert all(call.node is NODE for call in players_table.calls), \
            f"{who}: the search was not handed the context's own node"
    else:
        assert players_table.calls == [], f"{who}: a refused caller reached the search"


# ─────────────────────────────────────────── 2. the scope: unscoped whole, narrowed bounded

@pytest.mark.asyncio
async def test_a_scoped_caller_cannot_receive_a_player_outside_their_servers(
        config_dir, clean_registry, ban_bus, players_table):
    """ACCEPTANCE — the two halves of the rule, on one cluster, through the real app.

    ``HOME`` is online on the manager's own server, ``AWAY`` on somebody else's, and ``OFFLINE`` is on
    no server at all: the manager's answer is ``HOME`` and nothing else, and the bound the action
    handed the search is exactly the presence set of its own view (never a post-filter of the rows).
    """
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")                       # Admin: unscoped by rule
        admin = await lookup(http, "viper")
        await sign_in(http, "hoster")                         # a pure MANAGER of Host A
        held = await lookup(http, "viper")

    assert admin.status_code == 200 and held.status_code == 200
    assert ucids(admin) == [HOME, AWAY, OFFLINE] and "Benchwarmer" not in admin.text
    # the UNSCOPED caller hands the search no bound at all — the /info list, whole
    assert players_table.calls[0].present is None, "an unscoped caller's search was bounded"
    assert ucids(held) == [HOME], "a manager was handed a player outside their servers"
    assert "Viper Two" not in held.text and "Viper Three" not in held.text
    # the bound is the PRESENCE set of the caller's own view, as a WHERE term: BOTH players online on
    # the manager's server — the one the query matches and the one it does not.
    assert set(players_table.calls[1].present) == {HOME, BENCH}, \
        "the bound is not the caller's own servers' presence"
    assert players_table.calls[1].search == "viper"


@pytest.mark.asyncio
async def test_the_presence_bound_is_read_off_the_servers_the_caller_may_see(
        config_dir, clean_registry, ban_bus, players_table):
    """A second player online on the manager's own server is offered; the inactive one is not.

    ``SRS-1`` carries an ACTIVE ``HOME`` and a player merely CONNECTED (``active=False``): the bot's own
    "online right now" set is the active one (``Server.get_active_players``), so the second is not a
    suggestion — and the same manager's own view still bounds the answer.
    """
    cluster = hosted_fleet()
    cluster.servers[0].players[BENCH].active = False
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "hoster")
        response = await lookup(http, "viper")
    assert ucids(response) == [HOME]
    assert players_table.calls[0].present == [HOME], \
        "a merely connected player entered the presence bound"


def test_a_server_whose_players_cannot_be_read_contributes_nobody():
    """A server that cannot answer does not fail the search: it contributes no UCID to the bound."""

    class Unreachable:
        name = "SRS-B"

        def get_active_players(self):
            raise RuntimeError("the server is not reachable")

    ctx = types.SimpleNamespace(servers={
        "SRS-1": online(server("SRS-1"), (HOME, "Viper")),
        "SRS-B": Unreachable(),
    })
    assert _present_ucids(ctx) == [HOME]


# ────────────────────────────────────────────────────────── 3. the empty query

@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\t", "\n"])
async def test_an_empty_or_whitespace_query_returns_nothing(config_dir, clean_registry, ban_bus,
                                                            players_table, query):
    """ACCEPTANCE — an empty query is answered with NOTHING, and the search is not even reached.

    The ADD's field is empty when the dialog opens, and the console polls a field the operator is
    typing in: an unbounded answer here would print a fleet-wide list of names on every open.
    """
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await lookup(http, query)
    assert response.status_code == 200
    assert response.json() == {"players": []}
    assert players_table.calls == [], "an empty query reached the player search"


@pytest.mark.asyncio
async def test_the_action_itself_answers_an_empty_query_with_nothing(players_table):
    """The same rule at the seam, so no transport can get a dump a route forgot to guard."""
    for query in ("", "  ", None):
        result = await search_players(_transport_context("web"), query)
        assert result.success is True
        assert result.data == {"players": []}
    assert players_table.calls == []


@pytest.mark.asyncio
async def test_a_name_matching_nobody_is_an_empty_list_with_http_200(config_dir, clean_registry,
                                                                    ban_bus, players_table):
    """A search that matches nobody is not an error and not a guess: an empty list, HTTP 200."""
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await lookup(http, "nobody-here")
    assert response.status_code == 200
    assert response.json() == {"players": []}
    assert players_table.calls[0].search == "nobody-here"


# ────────────────────────────────────── 4. the limitation, and what still works instead

@pytest.mark.asyncio
async def test_a_narrowed_caller_cannot_autocomplete_an_offline_player_yet_bans_the_exact_ucid(
        config_dir, clean_registry, ban_bus, players_table):
    """THE STATED LIMITATION, asserted rather than described.

    ``Viper Three`` is in the table and offline: the manager's search cannot offer it (no server of
    theirs has it PRESENT, and ``players`` carries no server column to ask any other way). Typing its
    exact UCID still bans it — the no-JS path, unchanged: the field is a plain required text input and
    the write re-validates the UCID itself (``utils.is_ucid``), so the limitation costs convenience
    and never capability.
    """
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")
        found = await lookup(http, "Viper Three")
        assert found.status_code == 200 and found.json() == {"players": []}, \
            "a manager was offered a player who is not on their servers"
        assert OFFLINE not in players_table.calls[0].present, \
            "the offline player was excluded by a post-filter instead of the bound"
        reply = await add(http, ucid=OFFLINE, reason="cheating")
        html = await bans_page_html(http)
    assert reply.status_code == 303, reply.text
    rows = written(ban_bus)
    assert [(row["ucid"], row["server_name"]) for row in rows] == [(OFFLINE, "SRS-1")], \
        "the exact UCID a manager typed by hand was not banned on their own server"
    assert OFFLINE in html


# ──────────────────────────────────────────────── 5. the action's own refusals

@pytest.mark.asyncio
async def test_the_search_refuses_a_non_web_transport(players_table):
    """MCP / REST carry no scope: a service or unnamed context is refused before any read."""
    for ctx in (_transport_context(""),
                ActionContext.from_service(types.SimpleNamespace(node=types.SimpleNamespace()))):
        result = await search_players(ctx, "viper")
        assert result.success is False
        assert result.message == BANS_CONSOLE_ONLY
    assert players_table.calls == [], "a non-console caller reached the player search"


@pytest.mark.asyncio
async def test_a_search_that_cannot_be_read_is_a_typed_refusal_not_an_error(players_table):
    """The reused search raising (no pool, an unreachable table) is answered, never a 500."""
    players_table.fail = True
    result = await search_players(_transport_context("web"), "viper")
    assert result.success is False
    assert "could not be read" in result.message


@pytest.mark.asyncio
async def test_the_route_answers_an_absent_plugin_with_an_empty_list(config_dir, clean_registry,
                                                                    ban_bus, players_table):
    """The plugin is not loaded: the seam's typed refusal becomes an empty list, not a 500.

    The field's enhancement is an enhancement — a console without the mission plugin still renders the
    dialog, and the UCID is still typed by hand.
    """
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        from core import actions as actions_module
        saved = dict(actions_module._ACTIONS)
        actions_module._ACTIONS.clear()
        try:
            response = await lookup(http, "viper")
        finally:
            actions_module._ACTIONS.clear()
            actions_module._ACTIONS.update(saved)
    assert response.status_code == 200
    assert response.json() == {"players": []}
    assert players_table.calls == []


# ──────────────────────────────────────────── 6. the field's declaration and the page

@pytest.mark.asyncio
async def test_only_the_ucid_field_carries_the_lookup_and_the_dialog_loads_the_script(
        config_dir, clean_registry, ban_bus):
    """The declaration, on the rendered dialog: one ``data-lookup``, on the UCID box, plus the script."""
    app = build(config_dir, hosted_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(ADD_DIALOG, data={"_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    ucid_input = re.search(r'<input type="text" id="ucid-dialog"[^>]*>', text)
    assert ucid_input is not None, "the dialog asks for no ucid"
    assert f'data-lookup="{LOOKUP}"' in ucid_input.group(0), \
        "the UCID field does not carry the URL its enhancement reads"
    assert 'autocomplete="off"' in ucid_input.group(0), \
        "the browser's own history was left to compete with the suggestions"
    assert text.count("data-lookup=") == 1, "a second field claims a lookup"
    assert re.search(r'<script src="/static/player-lookup\.js(\?v=[^"]*)?" defer></script>', text), \
        "the dialog page does not load the shipped script"
    # the no-JS baseline is untouched: the field is still required and bounded by the same maximum
    assert "required" in ucid_input.group(0) and 'maxlength="64"' in ucid_input.group(0)


def test_the_lookup_is_a_field_record_that_defaults_to_none():
    """ONE builder: a field carries a lookup only when its declaration says so."""
    assert actions_page._dialog_field("who", "Who")["lookup"] == ""
    reason, days = actions_page.ban_dialog_fields(actions_page.BAN_ADD_ACTION)
    assert reason["lookup"] == "" and days["lookup"] == "", \
        "the shared ban fields gained a lookup"
    ucid_field = actions_page._dialog_field(actions_page.BAN_ADD_UCID_FIELD, "Player",
                                            required=True, lookup=actions_page.BAN_ADD_UCID_LOOKUP)
    assert ucid_field["lookup"] == LOOKUP


# ───────────────────────────────────── 7. the reused spelling's WHERE bound

class _StubCursor:
    def __init__(self, rows):
        self.rows = rows
        #: every ``(sql, params)`` the search executed, in order
        self.statements: list[tuple[str, object]] = []

    def execute(self, sql, params):
        self.statements.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubPool:
    def __init__(self, cursor):
        self._cursor = cursor

    def connection(self):
        return _StubConnection(self._cursor)


class _StubNode:
    """The node ``get_all_players`` is handed: a pool that records the statement it was given."""

    def __init__(self, rows):
        self.cursor = _StubCursor(rows)
        self.pool = _StubPool(self.cursor)

    def last(self) -> tuple[str, list]:
        """The last statement the search executed, and its parameters."""
        sql, params = self.cursor.statements[-1]
        return sql, params  # type: ignore[return-value]


def test_the_present_bound_is_a_where_term_and_the_cap_is_the_sqls_own():
    """The reused search: no bound means the whole match, a bound means ``= ANY`` on the SAME query.

    The empty presence set is a legal empty array (verified against PostgreSQL: it matches nobody)
    rather than a branch that could silently fall back to the unbounded list.
    """
    search = core_utils.get_all_players
    node = _StubNode([(HOME, "Viper")])

    assert search(node, search="viper") == [(HOME, "Viper")]
    sql, params = node.last()
    assert "= ANY(%s)" not in sql
    assert params == ["%viper%", "%viper%"]
    assert "WHERE length(p.ucid) = 32" in sql.replace("\n", " ")
    assert "ORDER BY last_seen DESC LIMIT 25" in sql, "the reused cap/order moved"

    search(node, search="viper", present=[HOME, AWAY])
    sql, params = node.last()
    assert "p.ucid = ANY(%s)" in sql, "the presence bound is not a WHERE term"
    assert params == ["%viper%", "%viper%", [HOME, AWAY]]

    search(node, search="viper", present=())
    sql, params = node.last()
    assert "p.ucid = ANY(%s)" in sql and params[-1] == [], \
        "an empty presence set did not stay a bound"


def test_an_exact_ucid_is_looked_up_by_ucidity_not_by_name():
    """``/info``'s own rule: a query that IS an ucid is matched as one, never as a name fragment."""
    search = core_utils.get_all_players
    node = _StubNode([(UCID, "Viper")])
    assert search(node, search=UCID) == [(UCID, "Viper")]
    sql, params = node.last()
    assert "p.ucid = %s" in sql
    assert params == [UCID]
    assert "ILIKE" not in sql


# ─────────────────────────────────────── 8. the shipped script, against a minimal DOM

PLAYER_LOOKUP_JS = Path(actions_page.__file__).parent.parent / "static" / "player-lookup.js"
HARNESS = Path(__file__).resolve().parent / "player_lookup_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def suggestions() -> dict:
    """The shipped script's own answers, booted against a minimal DOM by the harness."""
    from conftest import run_node_harness

    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert PLAYER_LOOKUP_JS.is_file(), "the shipped lookup script is missing"
    done = run_node_harness(["node", str(HARNESS), str(PLAYER_LOOKUP_JS)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["states"]


@requires_node
def test_a_typed_burst_asks_once_and_the_popup_carries_the_name_and_the_ucid(suggestions):
    """One request per burst (not one per key), and the popup is the pair the operator reads."""
    assert suggestions["initial"]["rows"] == []
    assert suggestions["initial"]["fetches"] == [], "the script fetched before anything was typed"
    assert suggestions["typed"]["fetches"] == [], "a request went out before the burst ended"
    assert suggestions["asked"]["fetches"] == [f"{LOOKUP}?q=vip"], \
        "one typed burst did not resolve to exactly one request"
    offered = suggestions["offered"]
    assert offered["list"]["hidden"] is False
    assert offered["list"]["role"] == "listbox"
    assert offered["list"]["label"] == "Player (UCID)", \
        "the popup is not named by the field's own label"
    assert offered["list"]["expanded"] == "true"
    assert offered["list"]["controls"] == offered["list"]["id"]
    assert offered["combobox"] == {"role": "combobox", "autocomplete": "list"}
    assert [row["name"] for row in offered["rows"]] == ["Viper", None]
    assert offered["rows"][0]["ucid"] == f"({'4b1f9c22' * 4})"
    assert [row["role"] for row in offered["rows"]] == ["option", "option"]
    assert [row["selected"] for row in offered["rows"]] == ["true", "false"], \
        "the first suggestion is not the one Enter would take"


@requires_node
def test_a_pick_writes_the_ucid_into_the_box(suggestions):
    """A press takes the row under the pointer; Down/Up move and Enter takes the highlighted one."""
    picked = suggestions["picked"]
    assert suggestions["press_event"]["prevented"] is True, \
        "the press was not held, so the box would lose focus before the pick"
    assert picked["value"] == "4b1f9c22" * 4, "a press did not write the row's UCID into the box"
    assert picked["list"]["hidden"] is True, "the popup stayed open after a pick"
    assert [row["selected"] for row in suggestions["after_down"]["rows"]] == ["false", "true"]
    assert [row["selected"] for row in suggestions["after_up"]["rows"]] == ["true", "false"]
    assert suggestions["enter_picked"]["value"] == "4b1f9c22" * 4, \
        "Enter did not take the highlighted suggestion's UCID"


@requires_node
def test_escape_closes_without_changing_the_box_and_enter_alone_submits(suggestions):
    """Escape puts the list away and leaves what was typed; with nothing offered Enter is the submit."""
    after_escape = suggestions["after_escape"]
    assert suggestions["escape"]["prevented"] is True
    assert after_escape["list"]["hidden"] is True and after_escape["rows"] == []
    assert after_escape["value"] == "vip", "Escape changed what the operator typed"
    assert suggestions["cleared"]["rows"] == []
    assert suggestions["enter_alone"]["prevented"] is False, \
        "Enter was swallowed with no suggestion offered: the dialog could not be submitted"


@requires_node
def test_an_empty_or_refused_answer_leaves_the_plain_box(suggestions):
    """Nobody matched, or the route refused: no popup, no error, the box as it was."""
    for state in ("empty", "refused"):
        answer = suggestions[state]
        assert answer["rows"] == [], f"{state}: a popup was shown"
        assert answer["list"]["hidden"] is True, f"{state}: the popup stayed open"
        assert answer["list"]["expanded"] == "false"


@requires_node
def test_a_stale_answer_is_ignored(suggestions):
    """The answer to a query the box has moved on from never paints over the current one."""
    assert suggestions["stale_ignored"]["rows"] == [], \
        "the answer to the abandoned query was painted"
    assert suggestions["stale_ignored"]["list"]["hidden"] is True
    taken = suggestions["answer_taken"]
    assert [row["name"] for row in taken["rows"]] == ["Answer To viper", "Another"], \
        "the answer that belongs to the box's current query was lost"


# ─────────────────────────────────────────────────── 9. the catalog carries the new sentence

#: The one new user-visible sentence this work adds (the search's own failure), in the domain the
#: action belongs to. Everything else it renders is operator data (a name, a UCID) or already
#: catalogued copy.
NEW_MSGID = "The player list could not be read: {}"
LANGUAGES = ("cn", "de", "es", "ru")


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_mission_catalog_carries_the_searchs_failure_sentence(language):
    """The source half: the ``.po`` holds the new msgid, with its one placeholder."""
    text = (REPO / "locale" / language / "LC_MESSAGES" / "mission.po").read_text(encoding="utf-8")
    assert f'msgid "{NEW_MSGID}"' in text, f"{language} mission.po lacks {NEW_MSGID!r}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_compiled_mission_catalog_resolves_the_searchs_failure_sentence(language):
    """The runtime half: the recompiled ``.mo`` serves it, and keeps the placeholder."""
    import gettext

    translations = gettext.translation("mission", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    resolved = translations.gettext(NEW_MSGID)
    assert resolved != NEW_MSGID, f"{language}: {NEW_MSGID!r} still renders English"
    assert resolved.count("{}") == NEW_MSGID.count("{}"), \
        f"{language}: the translation drops the placeholder: {resolved!r}"