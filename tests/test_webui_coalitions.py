"""The coalition passwords (blue / red) in the Configuration tab — the READ from the bot's database
and the WRITE through the bot's own ``setCoalitionPassword``.

Acceptance, one section per criterion: the two rows are their OWN declaration (unprotected, disjoint
from the editable and read-only sets); the read returns the cleartext plus which hashes are set and
NEVER the hash, refuses a DCS Admin and the REST/MCP transports, and applies the manager scope; the
write calls the bot's own method (never the hash), clears only on the explicit control, treats a blank
as unchanged, gates its own states, and audits in the Discord command's own words with no value
anywhere in the result; the console renders the three states with their honest notes and round-trips a
change end to end (hostile characters included). The read runs through ``core.actions.read_action``,
the UNGUARDED twin, so it works while a power action is in flight.
"""
from __future__ import annotations

import html as html_mod
import re
import types

import pytest

from conftest import asgi_client, permissions
from core import Status as CoreStatus
from core import actions as actions_module
from core.actions import action_registry, call_action, discover_actions
from core.data.const import Coalition
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels import serverconfig as server_config
from test_webui_dashboard import _hash, _login, _source, node
from test_webui_server_config import (  # noqa: F401 - fixtures/harness imported on purpose
    FakeSettings, _namespace_server, app_with, app_with_manager, config_dir, get,
)
from test_scheduler_server_config import (  # noqa: F401 - the action harness, one owner
    RecordingBot, audit_bot, ctx_for, discord_ctx, nameless_transport_ctx, plugin_ctx, registry, sa,
    service_ctx,
)

PAGE = "/fleet/SRS-1?tab=configuration"
COALITIONS = actions_page.SERVER_COALITIONS_PATH


# ---------------------------------------------------------------------------------- the doubles

class _Cursor:
    """The slice of a psycopg cursor the read uses: one ``fetchone``."""

    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    """The slice of a psycopg connection: ``execute`` returns a cursor over the fake row."""

    def __init__(self, db):
        self._db = db

    async def execute(self, sql, params=None):
        return _Cursor((self._db.get("blue"), self._db.get("red")))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """A pool whose ``connection()`` reads the double's own ``db`` mapping."""

    def __init__(self, db):
        self._db = db

    def connection(self):
        return _Conn(self._db)


class CoalitionServer:
    """A server double for the coalition face: a database row, settings, and the bot's own method.

    It models exactly what the actions read: ``apool`` answers the SELECT, ``settings['advanced']``
    carries the hashes, and ``setCoalitionPassword`` records its calls AND updates the fake row — so
    the end-to-end round-trip exercises the real action through the real route.
    """

    def __init__(self, name="SRS-1", *, status=CoreStatus.STOPPED, settings=None, locals_=None,
                 blue=None, red=None):
        self.name = name
        self.display_name = name
        self.status = status
        self.node = types.SimpleNamespace(name="dcs-master-01")
        self.instance = types.SimpleNamespace(name="Syria")
        self.port = 10308
        self.current_mission = None
        self.players = {}
        self.is_remote = False
        self.settings = FakeSettings(settings or {})
        self.locals = locals_ or {}
        self.db = {"blue": blue, "red": red}
        self.apool = _Pool(self.db)
        self.password_calls: list[tuple] = []

    async def setCoalitionPassword(self, coalition, password):
        self.password_calls.append((coalition, password))
        self.db["blue" if coalition == Coalition.BLUE else "red"] = password or None


def _app(config_dir, server, *, roles=("Admin",), username="staff"):
    """A console whose signed-in local account holds *roles*, over one coalition server."""
    return app_with(config_dir, list(roles), servers=[server], username=username)


async def _post_coalitions(app, data, username="staff"):
    """POST the coalition form (target + CSRF from the rendered page) and return (response, landed)."""
    async with asgi_client(app) as client:
        await _login(client, username)
        page = await client.get(PAGE)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        payload = {"server": "SRS-1", "_csrf_token": token}
        payload.update(data)
        response = await client.post(COALITIONS, data=payload)
        landed = await client.get(PAGE)
    return response, landed.text


# ----------------------------------------------------------- 1. the declaration (its own, disjoint)

def test_the_coalition_fields_are_their_own_unprotected_set():
    """Two plaintext fields, declared ``UNPROTECTED`` — never editable, never read-only, never advanced."""
    assert server_config.COALITION_TOKENS == ("blue", "red")
    assert tuple(server_config.coalition_keys()) == ("bluePassword", "redPassword")
    for field in server_config.COALITION_FIELDS:
        assert field.protection == server_config.UNPROTECTED, field.key
        assert field.readonly is False, field.key
    assert set(server_config.coalition_keys()).isdisjoint(server_config.editable_keys())
    assert set(server_config.coalition_keys()).isdisjoint(server_config.readonly_keys())


def test_the_read_models_declared_coalition_set_is_the_console_side_one():
    """The drift test's console-side set is the declaration's, not a second spelling."""
    assert set(server_config.coalition_keys()) == {"bluePassword", "redPassword"}


# ------------------------------------------------------------------- 2. the three states and notes

def test_coalition_views_render_each_state_with_its_own_note():
    """Known, none, hash-only and database-only each render truthfully (an empty field is not a lie)."""
    by_key = lambda rows: {row.key: row for row in rows}          # noqa: E731

    known = by_key(server_config.coalition_views(
        {"blue": "bpw", "red": "rpw", "blue_hash_set": True, "red_hash_set": True}))
    assert known["bluePassword"].value == "bpw" and known["bluePassword"].note == ""

    nothing = by_key(server_config.coalition_views(
        {"blue": None, "red": None, "blue_hash_set": False, "red_hash_set": False}))
    assert nothing["bluePassword"].value == "" and nothing["bluePassword"].note == ""

    hash_only = by_key(server_config.coalition_views(
        {"blue": None, "red": None, "blue_hash_set": True, "red_hash_set": False}))
    assert hash_only["bluePassword"].value == ""
    assert hash_only["bluePassword"].note == server_config.COALITION_UNKNOWN_NOTE
    assert hash_only["redPassword"].note == "", "only the set coalition carries the note"

    db_only = by_key(server_config.coalition_views(
        {"blue": "bpw", "red": None, "blue_hash_set": False, "red_hash_set": False}))
    assert db_only["bluePassword"].note == server_config.COALITION_DB_ONLY_NOTE


def test_coalition_views_are_empty_without_a_read():
    """No payload (the action absent, or refused) yields no rows — the card is then absent."""
    assert server_config.coalition_views(None) == ()
    assert server_config.coalition_views("not-a-mapping") == ()


# ------------------------------------------------------------------------- 3. the READ action

@pytest.mark.asyncio
async def test_the_read_returns_the_cleartext_and_the_hash_flags_never_the_hash(sa):
    server = CoalitionServer(
        settings={"advanced": {"bluePasswordHash": "BLUEHASH", "redPasswordHash": ""}},
        blue="blue-pw", red=None)
    result = await sa.get_server_coalitions(ctx_for([server]), "SRS-1")

    assert result.success is True
    assert result.data == {"blue": "blue-pw", "red": None,
                           "blue_hash_set": True, "red_hash_set": False}
    rendered = f"{result.message} {result.data} {result.to_dict()}"
    assert "BLUEHASH" not in rendered, "the hash must never be returned"


@pytest.mark.asyncio
async def test_the_read_refuses_a_dcs_admin_and_the_rest_and_mcp_transports(sa, registry):
    server = CoalitionServer()
    dcs_admin = await sa.get_server_coalitions(ctx_for([server], roles=("DCS Admin",)), "SRS-1")
    assert dcs_admin.success is False and dcs_admin.refused == sa.ADMIN_ONLY_SENTENCE

    rest = await sa.get_server_coalitions(plugin_ctx([server], registry), "SRS-1")
    assert rest.success is False and rest.refused == sa.COALITION_REST_REFUSAL
    assert rest.data == {}

    mcp = await sa.get_server_coalitions(service_ctx([server], registry), "SRS-1")
    assert mcp.success is False and mcp.refused == sa.SERVICE_REFUSAL


@pytest.mark.asyncio
async def test_a_manager_reads_their_own_coalitions_and_a_foreign_server_is_unresolved(sa):
    """The console's scoped bus holds only the manager's servers — a foreign name is NOT FOUND."""
    scope = types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=False)
    mine = CoalitionServer("Mine", locals_={"managed_by": ["Host A"]}, blue="bpw")
    # the caller's SCOPED view: only their own server (the console's _Cluster is built from it)
    ctx = ctx_for([mine], roles=(), scope=scope)

    own = await sa.get_server_coalitions(ctx, "Mine")
    assert own.success is True and own.data["blue"] == "bpw"

    foreign = await sa.get_server_coalitions(ctx, "Theirs")
    assert foreign.success is False and foreign.data == {}
    assert "not found" in foreign.refused


# ------------------------------------------------------------------------ 4. the WRITE action

@pytest.mark.asyncio
async def test_the_write_calls_the_bots_own_method_and_audits_in_the_discords_words(sa, audit_bot):
    server = CoalitionServer()
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1",
                                             {"bluePassword": "new-blue"})

    assert result.success is True
    assert server.password_calls == [(Coalition.BLUE, "new-blue")], "the bot's own method is the writer"
    assert result.message == sa.PASSWORD_CHANGED_SENTENCE
    # ONE entry, in the Discord command's own words, naming the coalition and never the value
    assert len(audit_bot.calls) == 1, audit_bot.calls
    event, audited = audit_bot.calls[0]
    assert event == "changed password for coalition blue", event
    assert audited == "SRS-1"
    assert "new-blue" not in f"{result.message} {result.to_dict()} {event}"


@pytest.mark.asyncio
async def test_a_change_to_both_writes_one_audit_entry_per_coalition(sa, audit_bot):
    """BOTH at once is TWO entries, in Discord's wording — one per coalition, never one line.

    The Discord command writes a line per coalition (``commands.py:1344``:
    ``changed password for coalition <blue|red>``); the console used to coalesce a two-coalition form
    into one entry reading ``for coalition blue, red``. Matching Discord, the trail now carries the
    same two lines — and the values appear in neither.
    """
    server = CoalitionServer()
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1",
                                             {"bluePassword": "blue-new", "redPassword": "red-new"})

    assert result.success is True
    assert [event for event, _ in audit_bot.calls] == [
        "changed password for coalition blue",
        "changed password for coalition red",
    ], audit_bot.calls
    assert [audited for _, audited in audit_bot.calls] == ["SRS-1", "SRS-1"]
    assert "blue-new" not in f"{result.to_dict()} {audit_bot.calls}"
    assert "red-new" not in f"{result.to_dict()} {audit_bot.calls}"


@pytest.mark.asyncio
async def test_the_write_redacts_the_applied_pair(sa, audit_bot):
    """``applied`` names the coalition with a set/unset marker — the two result shapes carry no value."""
    server = CoalitionServer()
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1",
                                             {"bluePassword": "s3cret", "redPassword": None})
    assert result.applied == {"bluePassword": {"to": "<set>"},
                              "redPassword": {"to": "<unset>"}}
    assert "s3cret" not in f"{result.message} {result.applied} {result.to_dict()}"


@pytest.mark.asyncio
async def test_none_is_the_explicit_clear_and_a_blank_is_unchanged(sa, audit_bot):
    server = CoalitionServer(blue="old")
    cleared = await sa.set_coalition_password(ctx_for([server]), "SRS-1", {"bluePassword": None})
    assert cleared.success is True and cleared.message == sa.PASSWORD_CLEARED_SENTENCE
    assert server.password_calls == [(Coalition.BLUE, "")], "a clear sends the empty password"

    other = CoalitionServer(red="keep")
    blank = await sa.set_coalition_password(ctx_for([other]), "SRS-1", {"redPassword": "   "})
    assert blank.success is False and other.password_calls == [], "a blank does nothing"
    assert "unchanged" in blank.skipped["redPassword"]
    assert other.db["red"] == "keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CoreStatus.SHUTDOWN, CoreStatus.STOPPED, CoreStatus.RUNNING,
                                    CoreStatus.PAUSED])
async def test_a_writable_state_allows_the_coalition_write(sa, audit_bot, status):
    server = CoalitionServer(status=status)
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1", {"bluePassword": "x"})
    assert result.success is True, f"{status} must be writable"
    assert server.password_calls == [(Coalition.BLUE, "x")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CoreStatus.LOADING, CoreStatus.SHUTTING_DOWN,
                                    CoreStatus.UNREGISTERED])
async def test_a_transitional_state_is_refused_with_its_own_sentence(sa, audit_bot, status):
    server = CoalitionServer(status=status)
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1", {"bluePassword": "x"})
    assert result.success is False
    assert sa.COALITION_STATE_SENTENCE in result.refused
    assert result.refused != sa.STOP_FIRST_SENTENCE, "the DCS Save's wording is not reused here"
    assert server.password_calls == []


@pytest.mark.asyncio
async def test_a_running_server_says_it_applies_at_the_next_restart(sa, audit_bot):
    server = CoalitionServer(status=CoreStatus.RUNNING)
    result = await sa.set_coalition_password(ctx_for([server]), "SRS-1", {"redPassword": "x"})
    assert result.message == sa.NEXT_RESTART_SENTENCE
    assert server.password_calls == [(Coalition.RED, "x")]


@pytest.mark.asyncio
async def test_the_write_refuses_the_rest_and_mcp_transports(sa, registry, audit_bot):
    server = CoalitionServer()
    for ctx, sentence in ((plugin_ctx([server], registry), sa.COALITION_REST_REFUSAL),
                          (service_ctx([server], registry), sa.SERVICE_REFUSAL)):
        result = await sa.set_coalition_password(ctx, "SRS-1", {"bluePassword": "x"})
        assert result.success is False and result.refused == sentence
        assert server.password_calls == [], "a refused caller never reaches the bot's method"


@pytest.mark.asyncio
async def test_an_unknown_or_unstamped_transport_is_refused_outright(sa, audit_bot):
    """CLOSED: a transport that is neither the console nor Discord is refused — even claiming Admin.

    The check is on the EXACT ``web``/``discord`` labels, so a hand-built context (``""``) or a future
    builder that forgets to stamp ``transport`` cannot fall through to the role check and be admitted
    by whatever roles it happens to carry. The refusal is the typed transport sentence — never the
    role check's ``ADMIN_ONLY_SENTENCE``, never a 500 — and nothing reaches the bot's method.
    """
    server = CoalitionServer()
    for transport in ("", "carrier-pigeon", "WEB", "Discord"):
        ctx = nameless_transport_ctx([server], transport=transport)
        ctx._roles = frozenset({"Admin"})          # the roles a fall-through WOULD have admitted

        read = await sa.get_server_coalitions(ctx, "SRS-1")
        assert read.success is False and read.refused == sa.COALITION_TRANSPORT_REFUSAL, transport
        assert read.data == {}

        write = await sa.set_coalition_password(ctx, "SRS-1", {"bluePassword": "x"})
        assert write.success is False and write.refused == sa.COALITION_TRANSPORT_REFUSAL, transport
        assert write.refused != sa.ADMIN_ONLY_SENTENCE, "the transport refusal, not a role check"

    assert server.password_calls == [], "a refused caller of any unknown transport never writes"


@pytest.mark.asyncio
async def test_the_console_and_discord_are_the_only_admitted_transports(sa, registry, audit_bot):
    """The closed door admits exactly the console (``web``) and Discord — each reaches the role check."""
    server = CoalitionServer(blue="bpw")

    web = ctx_for([server])                        # from_web → transport "web"
    assert web.transport == "web"
    assert (await sa.get_server_coalitions(web, "SRS-1")).success is True
    assert (await sa.set_coalition_password(web, "SRS-1", {"redPassword": "r"})).success is True

    discord = discord_ctx([server], registry, roles=("Admin",))    # from_interaction → "discord"
    assert discord.transport == "discord"
    assert (await sa.get_server_coalitions(discord, "SRS-1")).success is True
    assert (await sa.set_coalition_password(discord, "SRS-1", {"bluePassword": "b"})).success is True


@pytest.mark.asyncio
async def test_an_unknown_key_and_a_non_mapping_are_typed_refusals(sa, audit_bot):
    server = CoalitionServer()
    unknown = await sa.set_coalition_password(ctx_for([server]), "SRS-1", {"nope": "x"})
    assert unknown.success is False and "not a coalition password" in unknown.skipped["nope"]

    bad = await sa.set_coalition_password(ctx_for([server]), "SRS-1", ["bluePassword"])
    assert bad.success is False and "must be a mapping" in bad.skipped["values"]
    assert server.password_calls == []


@pytest.mark.asyncio
async def test_both_actions_are_registered(sa):
    registry = action_registry()
    assert "get_server_coalitions" in registry and "set_coalition_password" in registry


# -------------------------------------------------- 5. the read is UNGUARDED (the read_action seam)

@pytest.mark.asyncio
async def test_the_read_works_while_a_write_is_in_flight(sa, audit_bot):
    """``read_action`` takes no in-flight guard: a render must read during a power action."""
    server = CoalitionServer(blue="bpw")
    ctx = ctx_for([server])
    # put the target in flight, exactly as call_action's guard would
    actions_module._in_flight.add(actions_page.target_key("server", "SRS-1"))
    try:
        read = await actions_module.read_action("get_server_coalitions", ctx, server_name="SRS-1")
        assert read.success is True and read.data["blue"] == "bpw"
        # ...whereas the WRITE path meets the same guard and is refused
        blocked = await call_action("set_coalition_password", ctx, server_name="SRS-1",
                                    values={"bluePassword": "x"})
        assert blocked.success is False and "still running" in blocked.message
    finally:
        actions_module.reset_in_flight()


# ------------------------------------------------------------------ 6. the console: the card

def test_the_form_diff_clears_only_by_emptying_a_field_that_held_a_value():
    """The pure diff, one case per rule the docstring states."""
    current = {"blue": "bpw", "red": None, "blue_hash_set": True, "red_hash_set": False}
    # emptied a field that held a value: the deliberate clear
    assert actions_page.coalition_values_from_form(current, {"bluePassword": ""}) == \
        {"bluePassword": None}
    # emptied a field that shipped empty (the hash-only state): a no-op
    assert actions_page.coalition_values_from_form(current, {"redPassword": ""}) == {}
    # the value the page showed: unchanged
    assert actions_page.coalition_values_from_form(current, {"bluePassword": "bpw"}) == {}
    # a form that does not state a coalition field: clears nothing
    assert actions_page.coalition_values_from_form(current, {}) == {}
    # a new value is submitted verbatim
    assert actions_page.coalition_values_from_form(current, {"redPassword": "new"}) == \
        {"redPassword": "new"}


@pytest.mark.asyncio
async def test_the_page_shows_the_cleartext_and_never_the_hash(config_dir):
    server = CoalitionServer(settings={"advanced": {"bluePasswordHash": "BLUEHASH",
                                                    "redPasswordHash": "REDHASH"}},
                             blue="blue-pw", red="red-pw")
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])
    html = await get(app, PAGE)

    assert "Coalition passwords" in html
    assert 'name="bluePassword"' in html and 'name="redPassword"' in html
    assert 'value="blue-pw"' in html and 'value="red-pw"' in html
    assert "BLUEHASH" not in html and "REDHASH" not in html
    # both fields carry the eye, as the DCS password does
    assert 'data-reveal="cfg-bluePassword"' in html and 'data-reveal="cfg-redPassword"' in html
    # NO Clear control: the field itself is the clear (empty a field that held a value to remove it)
    assert "Clear the blue" not in html and "Clear the red" not in html
    assert 'name="bluePassword_clear"' not in html and 'name="redPassword_clear"' not in html


@pytest.mark.asyncio
async def test_the_coalition_card_contains_no_clear_control(config_dir):
    """The rendered card carries NO Clear control of any kind — no checkbox, no `_clear` field.

    Emptying a field that holds a value is the clear (the action-level test above pins the semantics),
    so the card offers nothing else. Scoped to the card by its heading so a bool field in the DCS face
    (which still renders a checkbox) cannot make this assertion ambiguous.
    """
    server = CoalitionServer(blue="bpw", red="rpw")
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])
    html = await get(app, PAGE)

    assert "Coalition passwords" in html
    card = html.split("Coalition passwords", 1)[1]
    assert "Clear the blue" not in card and "Clear the red" not in card, "no Clear control's wording"
    assert 'name="bluePassword_clear"' not in card, "no row posts a clear field"
    assert 'name="redPassword_clear"' not in card
    assert "> Clear the" not in card


@pytest.mark.asyncio
async def test_only_the_tabs_own_save_is_the_primary_control(config_dir):
    """ONE filled control on the page: the DCS face's Save. The other two read as secondary chips.

    the operator nearly missed the main Save because all three looked alike; this pins the fix — the tab's own
    Save is marked ``primary`` and neither ``Save channels`` nor ``Save coalition passwords`` is.
    """
    server = CoalitionServer(blue="bpw")
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])
    html = await get(app, PAGE)

    assert re.search(r'<button[^>]*class="[^"]*\bprimary\b[^"]*"[^>]*>Save</button>', html), \
        "the tab's own Save must be marked primary"
    for label in ("Save channels", "Save coalition passwords"):
        button = re.search(rf'<button[^>]*>{label}</button>', html)
        assert button, f"{label} must render on this page"
        assert "primary" not in button.group(0), f"{label} must read as secondary"


@pytest.mark.asyncio
async def test_the_page_renders_the_three_states_with_their_notes(config_dir):
    # a hash is set but the bot knows no cleartext
    app, _ = _app(config_dir, CoalitionServer(settings={"advanced": {"bluePasswordHash": "HASH"}}))
    discover_actions(["scheduler"])
    html = await get(app, PAGE)
    assert server_config.COALITION_UNKNOWN_NOTE in html

    # the bot knows it but DCS carries no hash
    app, _ = _app(config_dir, CoalitionServer(settings={"advanced": {}}, blue="bpw"))
    html = await get(app, PAGE)
    assert server_config.COALITION_DB_ONLY_NOTE in html

    # nothing set anywhere: empty fields and neither note
    app, _ = _app(config_dir, CoalitionServer(settings={"advanced": {}}))
    html = await get(app, PAGE)
    assert server_config.COALITION_UNKNOWN_NOTE not in html
    assert server_config.COALITION_DB_ONLY_NOTE not in html


@pytest.mark.asyncio
async def test_the_coalition_card_is_absent_without_the_action(config_dir):
    """No read action → no cleartext → NO card (never two rows reading as \"no password\")."""
    server = CoalitionServer(blue="bpw")
    app, _ = _app(config_dir, server)
    assert not actions_page.action_available("set_coalition_password")
    html = await get(app, PAGE)
    assert "Coalition passwords" not in html
    assert 'name="bluePassword"' not in html


# -------------------------------------------------------- 7. the console: set / clear / untouched

@pytest.mark.asyncio
async def test_the_value_round_trips_set_clear_and_untouched(config_dir):
    """set → shown; a re-post the page showed → unchanged; EMPTYING a set field → gone."""
    server = CoalitionServer(blue="old-blue", red="keep-red")
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])

    response, html = await _post_coalitions(app, {"bluePassword": "new-blue"})
    assert response.status_code == 303
    assert server.db["blue"] == "new-blue" and server.password_calls[-1] == (Coalition.BLUE, "new-blue")
    assert "Password changed." in html

    before = list(server.password_calls)
    await _post_coalitions(app, {"bluePassword": "new-blue"})
    assert server.password_calls == before, "the value the page showed is unchanged"
    assert server.db["blue"] == "new-blue"

    _response, html = await _post_coalitions(app, {"bluePassword": ""})
    assert server.db["blue"] is None, "emptying a field that held a value is the clear"
    assert server.password_calls[-1] == (Coalition.BLUE, "")
    assert "Password cleared." in html

    # a form that does not STATE a coalition field clears nothing
    before = list(server.password_calls)
    await _post_coalitions(app, {})
    assert server.password_calls == before and server.db["red"] == "keep-red"


@pytest.mark.asyncio
async def test_emptying_a_field_that_shipped_empty_does_nothing(config_dir):
    """FRANK'S STANDING RULE, narrowed: a blank on a field that held NOTHING is a no-op."""
    server = CoalitionServer(red="")                 # shipped empty
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])

    await _post_coalitions(app, {"redPassword": ""})
    assert server.password_calls == [], "an empty field's blank must not reach the bot's method"
    assert server.db["red"] in (None, "")


@pytest.mark.asyncio
async def test_a_password_the_bot_does_not_know_cannot_be_removed_from_the_console(config_dir):
    """The hash-set-with-no-cleartext state: the field renders EMPTY, so no submit can wipe it.

    This is the case the removed ``Clear`` control used to cover. Without it, a password the bot knows
    only as a hash can be REPLACED but not REMOVED from the console: emptying the (already empty) field
    does nothing, and a form that omits the field clears nothing — the password can never be wiped by
    accident. The card still says the state (:data:`COALITION_UNKNOWN_NOTE`).
    """
    server = CoalitionServer(settings={"advanced": {"bluePasswordHash": "HASH"}})
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])

    html = await get(app, PAGE)
    assert server_config.COALITION_UNKNOWN_NOTE in html

    _response, html = await _post_coalitions(app, {"bluePassword": ""})
    assert server.password_calls == [], "a blank cannot remove a hash-only password"
    assert server.db["blue"] is None

    # ...and a NEW value still replaces it
    await _post_coalitions(app, {"bluePassword": "fresh-blue"})
    assert server.password_calls[-1] == (Coalition.BLUE, "fresh-blue")


@pytest.mark.asyncio
async def test_a_hostile_password_is_escaped_and_round_trips(config_dir):
    """A quote, a bracket and an ampersand must not break the attribute, and must land verbatim."""
    hostile = '"</script><b>&\''
    server = CoalitionServer(blue=hostile)
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])
    html = await get(app, PAGE)

    match = re.search(r'<input type="password" id="cfg-bluePassword" name="bluePassword" '
                      r'class="cfg-input"\s+value="([^"]*)"', html)
    assert match, "the field must still render (a quote must not close the attribute early)"
    raw = match.group(1)
    assert '"' not in raw and "'" not in raw, f"the attribute still carries a raw quote: {raw!r}"
    assert "&lt;/script&gt;&lt;b&gt;&amp;" in html, "the brackets and ampersand must be escaped"
    assert hostile not in html, "the raw hostile value must not appear unescaped anywhere"
    assert html_mod.unescape(raw) == hostile, "the attribute must round-trip the stored password"

    # ...and a POST carries it into the database byte-identically
    response, _ = await _post_coalitions(app, {"bluePassword": hostile + "2"})
    assert response.status_code == 303
    assert server.db["blue"] == hostile + "2"


# ------------------------------------------------------------------ 8. the console: the scoping

@pytest.mark.asyncio
async def test_a_manager_reaches_only_their_own_servers_coalitions(config_dir):
    """A manager's page shows their own server's card; another hoster's server is the console's 403."""
    from test_webui_server_config import _manager_server

    mine = _manager_server(name="Mine", settings={"advanced": {"bluePasswordHash": "HASH"}})
    theirs = _namespace_server(name="Theirs", locals_={"managed_by": ["Host B"]})
    app, _ = app_with_manager(config_dir, servers=[mine, theirs])
    discover_actions(["scheduler"])

    own = await get(app, "/fleet/Mine?tab=configuration", username="manager")
    assert "Coalition passwords" in own, "a manager reads their own server's coalition card"

    refused = await get(app, "/fleet/Theirs?tab=configuration", username="manager", expect=403)
    assert "Not authorized" in refused

    # ...and a CRAFTED POST naming the foreign server is the same bare 403, nothing reaches the action
    async with asgi_client(app) as client:
        await _login(client, "manager")
        page = await client.get("/fleet/Mine?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(COALITIONS, data={"server": "Theirs", "bluePassword": "x",
                                                       "_csrf_token": token})
    assert response.status_code == 403


# ------------------------------------------------------------------ 9. the declaration and route

def test_the_coalition_write_has_its_own_scoped_admin_capability():
    actions_page.declare()
    detail_page.capabilities()
    action = actions_page.SERVER_COALITION_ACTIONS[0]
    assert action.qualname == "set_coalition_password"
    assert action.capability == actions_page.SERVER_COALITIONS_CAPABILITY == "servers.config.coalitions"
    assert action.capability != actions_page.SERVER_CONFIG_CAPABILITY
    assert action.path == COALITIONS
    assert action.statuses == ("SHUTDOWN", "STOPPED", "RUNNING", "PAUSED"), "its own state gate"
    assert permissions.roles_for_capability(action.capability) == ("Admin",)
    assert permissions.scope_grants(action.capability) is True


@pytest.mark.asyncio
async def test_the_coalition_route_is_post_only_with_its_own_capability(config_dir):
    server = CoalitionServer()
    app, registrar = _app(config_dir, server)
    routes = [r for r in registrar.routes if getattr(r, "path", None) == COALITIONS]
    assert routes, "the coalition write route is registered"
    for route in routes:
        assert set(route.methods or ()) == {"POST"}, "the coalition write is a POST, never a GET"
    assert registrar.capability_for(path=COALITIONS) == actions_page.SERVER_COALITIONS_CAPABILITY


@pytest.mark.asyncio
async def test_the_tab_points_at_the_three_declared_write_paths(config_dir):
    server = CoalitionServer()
    app, _ = _app(config_dir, server)
    discover_actions(["scheduler"])
    html = await get(app, PAGE)
    assert set(re.findall(r'action="(/actions/[^"]+)"', html)) == {
        actions_page.SERVER_CONFIG_PATH, actions_page.SERVER_CHANNELS_PATH,
        actions_page.SERVER_COALITIONS_PATH}


@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_the_coalition_write_by_the_gate(config_dir):
    server = CoalitionServer()
    app, _ = _app(config_dir, server, roles=("DCS Admin",))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.post(COALITIONS, data={"server": "SRS-1"})
    assert response.status_code == 403, "the gate refuses the coalition write capability"
