"""The Bans page: the scoped ban LIST and the LIFT, end to end.

One section per acceptance criterion of the card:

* the read is built FROM the caller's target set — an Admin reads every ban, a scoped holder only
  the rows on their managed servers — and an unknown ucid renders the UCID, never a blank;
* a lift POST with no one-shot confirm token is REFUSED and changes no row (that refusal IS the
  modal gate);
* a lift of a PARTIALLY-owned entry expires only the caller's rows;
* a permanent ban renders ``never`` (``banned_until.year == 9999``);
* a DGSA entry wears the word ``DGSA`` and is liftable;
* the door map: only the declared paths (and their ``/confirm`` twins) can reach a ban write, each
  with its declared capability, and ``plugins/restapi`` still has no ban route.

Driven through the REAL application (the production factory, the shell, the local login backend and
a stub source), with the ban list behind a stub ServiceBus — the domain boundary the page and the
action both read.
"""
from __future__ import annotations

import datetime
import gettext
import re
import types
from pathlib import Path

import pytest

from core.actions import ActionContext
from core.services.registry import ServiceRegistry
from plugins.mission.actions import BANS_CONSOLE_ONLY, list_bans, unban
from services.servicebus import ServiceBus
from services.webservice import readmodels
from services.webservice.readmodels.bans import expires_utc as ban_expires_utc
from services.webservice.pages import actions as actions_page
from services.webservice.pages import bans as bans_page
from services.webservice.shell import create_app, install_shell
from test_webui_write_pause import (  # noqa: F401 - fixtures reused on purpose
    PASSWORD, build, clean_registry, client, config_dir, fleet, recording_bot, server, sign_in,
    token_of,
)

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 6, 1, 12, 0)
LATER = datetime.datetime(2027, 6, 1, 12, 0)
NEVER = datetime.datetime(9999, 12, 31)

#: The DGSA badge EXACTLY as the table renders it: the word in the name cell WITH the tooltip the
#: design of record draws (`mockups/bans-dashboard`: `title="Held in the DGSA global ban list, shared
#: by every installation that syncs it"`). The tag is a proper noun (never translated); the tooltip
#: is chrome and is.
DGSA_TAG = ('<span class="tag" title="Held in the DGSA global ban list, shared by every installation '
            'that syncs it">DGSA</span>')

#: The row's controls are the console's GLYPH STRIP now (``_strip.html``), so a control is pinned by
#: its native tooltip and its accessible name — the target's name is operator data and rides in the
#: aria-label. The row carries no visible label any more (the dialog's primary button still does).
LIFT_TITLE = 'title="Lift — remove this ban from the list"'


def _row(ucid, *, name=None, server_name=None, banned_by="mod", reason="cheating",
         banned_at: object = NOW, banned_until: object = LATER):
    return {"ucid": ucid, "discord_id": -1, "name": name, "banned_by": banned_by,
            "reason": reason, "banned_at": banned_at, "banned_until": banned_until,
            "server_name": server_name}


class FakeBus:
    """A stub ServiceBus carrying ban ROWS (one per ucid+server), grouped like the real one.

    ``bans(servers=...)`` reproduces the bus's own grouping — the page must never see the raw rows —
    and ``unban(ucid, server_name)`` expires exactly the matching rows, which is what the "partially
    owned" assertion reads.
    """

    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.bans_calls: list = []
        self.unbans: list = []
        self.bans_written: list = []

    async def ban(self, ucid, banned_by, reason="n/a", days=None, server_name=None,
                  banned_at=None, until=None):
        """The ONE upsert the ADD and the EDIT perform, recorded and mirrored onto the row set.

        Same signature and same sentinel as the real bus (``ServiceBus.ban``): an explicit ``until``
        (the EDIT's absolute UTC end) is written verbatim, ``days`` is measured from UTC NOW, and an
        empty duration writes the year-9999 ``banned_until``; the row identity is
        ``(ucid, server_name)``. Both paths write NAIVE (UTC) stamps.
        """
        self.bans_written.append({"ucid": ucid, "banned_by": banned_by, "reason": reason,
                                  "days": days, "until": until, "server_name": server_name,
                                  "banned_at": banned_at})
        stamp = (banned_at or NOW).replace(tzinfo=None)
        # ``until`` (the EDIT's absolute end) wins when given and is written as-is; otherwise the DAY
        # COUNT is measured from UTC NOW, exactly like ``ServiceBus.ban``
        # (``datetime.now(tz=timezone.utc) + timedelta(days=days)``, stored NAIVE) — only ``banned_at``
        # is taken from the caller, which is what lets an EDIT re-write an entry without moving it in
        # time. Local time here would add the host's UTC offset to every written expiry.
        if until is not None:
            end = until.replace(tzinfo=None)
        elif not days:
            end = NEVER
        else:
            end = (datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(days=days)).replace(tzinfo=None)
        for row in self.rows:
            if row["ucid"] == ucid and row["server_name"] == server_name:
                row.update(banned_by=banned_by, reason=reason, banned_at=stamp,
                           banned_until=end)
                return
        self.rows.append(_row(ucid, name=None, server_name=server_name, banned_by=banned_by,
                              reason=reason, banned_at=stamp, banned_until=end))

    async def bans(self, *, expired=False, servers=None):
        self.bans_calls.append(servers)
        names = None if servers is None else set(servers)
        grouped: dict = {}
        for row in self.rows:
            if names is not None and row["server_name"] not in names:
                continue
            key = (row["ucid"], row["banned_at"], row["reason"])
            ban = grouped.get(key)
            if ban is None:
                ban = dict(row)
                ban["server_name"] = None if row["server_name"] is None else [row["server_name"]]
                grouped[key] = ban
            elif row["server_name"] is None:
                ban["server_name"] = None
            elif ban["server_name"] is not None:
                ban["server_name"].append(row["server_name"])
        return list(grouped.values())

    async def unban(self, ucid, server_name=None):
        self.unbans.append((ucid, server_name))
        cleared = 0
        for row in self.rows:
            if row["ucid"] != ucid:
                continue
            if server_name is not None and row["server_name"] != server_name:
                continue
            if row["banned_until"] > NOW:
                row["banned_until"] = NOW
                cleared += 1
        return cleared

    def active(self, ucid, server_name=None):
        return [row for row in self.rows
                if row["ucid"] == ucid and row["banned_until"] > NOW
                and (server_name is None or row["server_name"] == server_name)]


@pytest.fixture
def ban_bus():
    """Install a stub bus under the real ``ServiceBus`` key (``_ban_service`` resolves it there)."""
    saved = ServiceRegistry._singletons.get(ServiceBus)
    bus = FakeBus([])
    ServiceRegistry._singletons[ServiceBus] = bus
    try:
        yield bus
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


async def bans_page_html(http):
    response = await http.get(bans_page.BANS_PATH)
    assert response.status_code == 200, response.text
    return response.text


async def lift(http, ucid, banned_at_key):
    """Run the WHOLE lift: the dialog (which mints the one-shot token), then the action."""
    page = await bans_page_html(http)
    dialog = await http.post(actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX,
                             data={"ucid": ucid, "banned_at": banned_at_key,
                                   "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    token = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
    return await http.post(actions_page.BAN_LIFT_PATH,
                           data={"ucid": ucid, "banned_at": banned_at_key,
                                 "_confirm_token": token, "_csrf_token": token_of(page)})


# --------------------------------------------------------------- 1. the scoped read


@pytest.mark.asyncio
async def test_an_admin_reads_every_ban_and_a_scoped_holder_only_their_servers(
        config_dir, clean_registry, ban_bus):
    """The read is built FROM the caller's target set: Admin unscoped, hoster scoped."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1"),
                    _row("u-b", name="Bravo", server_name="SRS-B")]
    app = build(config_dir, fleet())

    async with client(app) as http:
        await sign_in(http, "specialk")                     # Admin: unscoped by rule
        admin = await bans_page_html(http)
        await sign_in(http, "hoster")                       # a pure MANAGER of Host A
        hosted = await bans_page_html(http)

    assert "Alpha" in admin and "Bravo" in admin
    assert ban_bus.bans_calls == [None, ("SRS-1",)]
    assert "Alpha" in hosted and "Bravo" not in hosted, "a manager saw a foreign server's ban"


@pytest.mark.asyncio
async def test_an_unknown_ucid_renders_the_ucid_never_a_blank(config_dir, clean_registry, ban_bus):
    """The name is the players table's, and an unknown ucid falls back to the UCID."""
    ban_bus.rows = [_row("4b1f9c22-unknown", name=None, server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert "4b1f9c22-unknown" in html
    assert "<b></b>" not in html, "an unknown ucid rendered a blank name cell"


# --------------------------------------------------------------- 2. the confirm gate


@pytest.mark.asyncio
async def test_a_lift_without_a_confirm_token_is_refused_and_changes_no_row(
        config_dir, clean_registry, ban_bus, recording_bot):
    """A POST that skipped the dialog has no token: the action's route refuses it, and nothing moves."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        reply = await http.post(actions_page.BAN_LIFT_PATH,
                                data={"ucid": "u-a", "banned_at": NOW.isoformat(),
                                      "_csrf_token": token_of(page)})
    assert reply.status_code == 403
    assert ban_bus.unbans == [], "a tokenless POST reached the action"
    assert ban_bus.active("u-a", "SRS-1"), "the ban was lifted without its confirmation"


# --------------------------------------------------------------- 3. per-row expiry


@pytest.mark.asyncio
async def test_lifting_a_partially_owned_entry_expires_only_the_callers_rows(
        config_dir, clean_registry, ban_bus, recording_bot):
    """The one ban covers SRS-1 and SRS-B; the Host A manager lifts only their row."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1"),
                    _row("u-a", name="Alpha", server_name="SRS-B")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")
        reply = await lift(http, "u-a", NOW.isoformat())
    assert reply.status_code == 303
    assert ban_bus.unbans == [("u-a", "SRS-1")], "the lift touched a server outside the caller's set"
    assert ban_bus.active("u-a", "SRS-1") == []
    assert ban_bus.active("u-a", "SRS-B"), "the foreign server's row must stay banned"


# --------------------------------------------------------------- 4. the permanent sentinel


@pytest.mark.asyncio
async def test_a_permanent_ban_renders_never(config_dir, clean_registry, ban_bus):
    """``banned_until.year == 9999`` renders the word ``never`` in the state chip."""
    ban_bus.rows = [_row("u-perm", name="Forever", server_name="SRS-1", banned_until=NEVER)]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert readmodels.permanent(ban_bus.rows[0]) is True
    assert '<span class="tag">never</span>' in html
    assert "9999" not in html, "the raw sentinel leaked into the page"


# --------------------------------------------------------------- 5. DGSA


@pytest.mark.asyncio
async def test_a_dgsa_entry_wears_the_word_and_is_liftable(config_dir, clean_registry, ban_bus,
                                                           recording_bot):
    """A DGSA entry (``banned_by == 'cloud'``) is badged and liftable locally once shown."""
    ban_bus.rows = [_row("u-dgsa", name="Banned", server_name=None, banned_by="cloud",
                         reason="DGSA: cheating")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(bans_page.BANS_PATH + "?dgsa=1")).text
        assert DGSA_TAG in html
        assert LIFT_TITLE in html, "a DGSA row offered no lift control"
        assert "Lift DGSA" not in html, "the lift must read the SAME on every row"
        reply = await lift(http, "u-dgsa", NOW.isoformat())
    assert reply.status_code == 303
    assert ban_bus.active("u-dgsa") == []


@pytest.mark.asyncio
async def test_the_dgsa_lift_dialog_says_it_is_local_only(config_dir, clean_registry, ban_bus):
    """The DGSA dialog's copy states ONE thing: the lift is LOCAL and the cloud ban is untouched."""
    ban_bus.rows = [_row("u-dgsa", name="Banned", server_name=None, banned_by="cloud")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await bans_page_html(http)
        dialog = await http.post(actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX,
                                 data={"ucid": "u-dgsa", "banned_at": NOW.isoformat(),
                                       "_csrf_token": token_of(page)})
    assert dialog.status_code == 200
    assert "applies here only" in dialog.text
    assert "DGSA cloud ban is not affected by it" in dialog.text
    # NOTHING about re-applying: the sync only re-applies a NEWER shared entry, so the sentence that
    # claimed the ban "returns if the entry changes" was a second, wrong meaning.
    assert "re-apply" not in dialog.text and "re-applies" not in dialog.text
    assert "returns if the entry changes" not in dialog.text
    assert "Lift ban…" in dialog.text and "Lift DGSA" not in dialog.text, \
        "the dialog title must read the SAME as an ordinary lift's"


# --------------------------------------------------------------- 6. the door map


def _console_registrar():
    app = create_app()
    return install_shell(app, node=types.SimpleNamespace(master=True), config={})


def test_only_the_declared_paths_reach_a_ban_write():
    """The ban-write surface is exactly the declared paths, each with its declared capability.

    Both halves of the ban surface are covered: the LIFT (the row control) and the ADD (the page
    level control), each contributing its POST and the POST that renders its dialog — plus the ADD's
    own player SEARCH (a GET, declared with the ADD's capability: the door to the search is the door
    to the write it feeds).
    """
    registrar = _console_registrar()
    declared = {actions_page.BAN_LIFT_PATH: actions_page.BAN_LIFT_CAPABILITY,
                actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX:
                    actions_page.BAN_LIFT_CAPABILITY,
                actions_page.BAN_ADD_PATH: actions_page.BAN_ADD_CAPABILITY,
                actions_page.BAN_ADD_PATH + actions_page.CONFIRM_SUFFIX:
                    actions_page.BAN_ADD_CAPABILITY,
                actions_page.BAN_PLAYERS_PATH: actions_page.BAN_ADD_CAPABILITY}
    ban_routes = [route for route in registrar.routes
                  if "ban" in (getattr(route, "path", "") or "")]
    seen = set()
    for route in ban_routes:
        path = route.path
        seen.add(path)
        assert registrar.capability_for(route=route), f"{path} reaches a ban write undeclared"
    # the player Ban control (the existing Discord-equivalent write) is the only other ban route,
    # and it declares players.ban; the LIFT's two paths are the only players.unban ones.
    assert "/actions/player/ban" in seen, "the existing player Ban control lost its route"
    for path, capability in declared.items():
        assert path in seen, f"the declared ban path {path} is not registered"
        assert registrar.capability_for(path=path) == capability
    unban_paths = {path for path in seen
                   if registrar.capability_for(path=path) == actions_page.BAN_LIFT_CAPABILITY}
    assert unban_paths == {actions_page.BAN_LIFT_PATH,
                           actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX}, \
        "an undeclared path carries the lift capability"
    # EVERY path carrying players.ban is a declared one: the player ROW control (its own two paths),
    # the ADD's two, and the ADD's own player search.
    add_paths = {path for path in seen
                 if registrar.capability_for(path=path) == actions_page.BAN_ADD_CAPABILITY}
    assert add_paths == {"/actions/player/ban", "/actions/player/ban" + actions_page.CONFIRM_SUFFIX,
                         actions_page.BAN_ADD_PATH,
                         actions_page.BAN_ADD_PATH + actions_page.CONFIRM_SUFFIX,
                         actions_page.BAN_PLAYERS_PATH}, \
        "an undeclared path carries the add-ban capability"


def test_the_restapi_plugin_still_has_no_ban_route():
    """The bot-facing REST contract is untouched: nothing in ``plugins/restapi`` writes a ban."""
    source = (REPO / "plugins" / "restapi" / "commands.py").read_text(encoding="utf-8")
    for forbidden in ('"/ban"', "'/ban'", "ban_ucid", "list_bans", "unban("):
        assert forbidden not in source, f"plugins/restapi grew a ban door: {forbidden}"


# --------------------------------------------------------------- 7. the DGSA marker is pinned


def test_the_dgsa_marker_is_the_cloud_plugins_own_name():
    """``DGSA_BANNED_BY`` must be the name the cloud plugin declares, or the badge dies silently.

    The console keeps its own copy of the word because it may not import a plugin module; this pin is
    what keeps that copy honest. ``core/plugin.py`` derives ``plugin_name`` from the plugin's OWN
    module path (``type(self).__module__.split('.')[-2]``), so that expression — read off the real
    class — is the tree's source of truth, never a second literal. Renaming ``plugins/cloud`` moves
    it and this test fails.
    """
    from plugins.cloud.commands import Cloud

    declared = Cloud.__module__.split(".")[-2]
    assert readmodels.DGSA_BANNED_BY == declared


# --------------------------------------------------------------- 8. the web-only transport guard


def _transport_context(transport):
    """A context whose transport is set and nothing else — the guard reads ``ctx.transport`` alone."""
    ctx = ActionContext(node=types.SimpleNamespace(), bus=types.SimpleNamespace(servers={}))
    ctx._transport = transport
    return ctx


@pytest.mark.asyncio
async def test_list_bans_refuses_an_unnamed_transport(ban_bus):
    """A hand-built context names no transport: the read fails CLOSED and never reaches the bus."""
    result = await list_bans(_transport_context(""))
    assert result.success is False
    assert result.message == BANS_CONSOLE_ONLY
    assert ban_bus.bans_calls == [], "the refused read still reached the bus"


@pytest.mark.asyncio
async def test_list_bans_refuses_the_service_transport(ban_bus):
    """MCP wraps every ``@action`` as a tool: the service transport must not read a fleet-wide list."""
    service = types.SimpleNamespace(node=types.SimpleNamespace())
    result = await list_bans(ActionContext.from_service(service))
    assert result.success is False
    assert result.message == BANS_CONSOLE_ONLY
    assert ban_bus.bans_calls == [], "the refused read still reached the bus"


@pytest.mark.asyncio
async def test_unban_refuses_an_unnamed_transport(ban_bus):
    """The guard is the only barrier between a scopeless caller and a fleet-wide unban."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1")]
    result = await unban(_transport_context(""), ucid="u-a", banned_at=NOW.isoformat())
    assert result.success is False
    assert result.message == BANS_CONSOLE_ONLY
    assert ban_bus.unbans == [], "the refused lift still reached the bus"
    assert ban_bus.active("u-a", "SRS-1"), "a non-console caller lifted the ban"


@pytest.mark.asyncio
async def test_unban_refuses_the_service_transport(ban_bus):
    """The MCP tool form of the lift must be refused before any row is expired."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1")]
    service = types.SimpleNamespace(node=types.SimpleNamespace())
    result = await unban(ActionContext.from_service(service), ucid="u-a", banned_at=NOW.isoformat())
    assert result.success is False
    assert result.message == BANS_CONSOLE_ONLY
    assert ban_bus.unbans == [], "the refused lift still reached the bus"
    assert ban_bus.active("u-a", "SRS-1"), "a non-console caller lifted the ban"


# --------------------------------------------------------------- 9. the entry identity is total


def test_an_entry_without_a_readable_timestamp_is_not_a_row():
    """An unreadable ``banned_at`` carries no identity, so it is not rendered.

    Pre-change ``ban_views`` returned it with ``banned_at_key == ""`` and the key ``<ucid>@`` — which
    the LIFT's confirm re-read matches for ANY form posting an empty ``banned_at``
    (``pages/actions.py``: ``candidate.banned_at_key == banned_at``). Dropping it keeps every
    rendered row's identity total and unique.
    """
    rows = readmodels.ban_views([
        _row("u-a", name="Alpha", server_name="SRS-1", banned_at="2026-06-01T12:00:00"),
        _row("u-b", name="Bravo", server_name="SRS-1"),
    ])
    assert [row.ucid for row in rows] == ["u-b"]
    assert all(row.banned_at_key for row in rows), "a rendered row carried an empty identity"


# --------------------------------------------------------------- 10. the DGSA view option

#: Two BOT-WIDE DGSA entries (``banned_by == 'cloud'``, no server) beside one ordinary scoped ban.
DGSA_ROWS = (
    _row("u-a", name="Alpha", server_name="SRS-1"),
    _row("u-d1", name="GlobalOne", server_name=None, banned_by="cloud", reason="DGSA: aimbot"),
    _row("u-d2", name="GlobalTwo", server_name=None, banned_by="cloud", reason="DGSA: racism"),
)


def hidden_note(html: str) -> int | None:
    """The withheld count the page states, or ``None`` when it states none."""
    match = re.search(r"(\d+) DGSA bans? hidden", html)
    return int(match.group(1)) if match else None


def _hide_link(html: str) -> str | None:
    """The link the SHOWN state offers back out of the DGSA view, or ``None``."""
    match = re.search(r'href="(/bans[^"]*)">Hide DGSA bans</a>', html)
    return match.group(1) if match else None


@pytest.mark.asyncio
async def test_dgsa_rows_are_withheld_by_default_and_the_count_is_stated(
        config_dir, clean_registry, ban_bus):
    """The opening page renders no DGSA row and SAYS how many it withheld."""
    ban_bus.rows = list(DGSA_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert "Alpha" in html, "the ordinary ban vanished with the DGSA ones"
    assert "GlobalOne" not in html and "GlobalTwo" not in html, "a DGSA row was rendered"
    assert DGSA_TAG not in html, "a DGSA tag was rendered"
    assert LIFT_TITLE in html, "the ordinary row lost its lift"
    assert hidden_note(html) == 2, "the page did not state the withheld count"


@pytest.mark.asyncio
async def test_the_dgsa_param_renders_them(config_dir, clean_registry, ban_bus):
    """``?dgsa=1`` reveals the DGSA rows, tag and lift included, and states the inverse instead."""
    ban_bus.rows = list(DGSA_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(bans_page.BANS_PATH + "?dgsa=1")).text
    assert "GlobalOne" in html and "GlobalTwo" in html, "the DGSA rows were still withheld"
    assert DGSA_TAG in html, "a DGSA row lost its tag"
    assert LIFT_TITLE in html, "a DGSA row lost its lift"
    assert "DGSA bans shown" in html, "the shown state does not say so"
    hide = _hide_link(html)
    assert hide is not None, "the shown state offers no way back"
    assert "dgsa" not in hide, "the link back still asks for the DGSA rows"
    assert hidden_note(html) is None, "a note was rendered with nothing hidden"


@pytest.mark.asyncio
async def test_no_dgsa_entries_means_no_note(config_dir, clean_registry, ban_bus):
    """A view holding no DGSA entry states no withheld count and carries no view option at all."""
    ban_bus.rows = [_row("u-a", name="Alpha", server_name="SRS-1")]
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await bans_page_html(http)
    assert hidden_note(html) is None, "the page stated a count with nothing withheld"
    assert "DGSA bans hidden" not in html
    assert "Show DGSA bans" not in html, "the view option rendered with nothing to reveal"


@pytest.mark.asyncio
async def test_a_narrowed_caller_sees_no_dgsa_row_and_no_foreign_count(
        config_dir, clean_registry, ban_bus):
    """The toggle filters WITHIN the caller's scoped set: a manager sees no DGSA row either way.

    The DGSA rows are bot-wide, so the scoped read never brings them to a ``managed_by`` holder —
    and the withheld count is taken from that same set, so it cannot disclose a ban outside it.
    """
    ban_bus.rows = list(DGSA_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")                    # a pure MANAGER of Host A
        off = await bans_page_html(http)
        on = (await http.get(bans_page.BANS_PATH + "?dgsa=1")).text
    for html in (off, on):
        assert "GlobalOne" not in html and "GlobalTwo" not in html, \
            "a manager saw a bot-wide DGSA row"
        assert hidden_note(html) is None, "the count disclosed a ban outside the caller's scope"
    assert "Alpha" in off, "the manager lost their own ban"
    assert "DGSA bans shown" in on, "the shown state was not rendered for the manager"


# --------------------------------------------------------------- 11. the stub's own clock


@pytest.mark.asyncio
async def test_the_stub_measures_the_day_count_from_utc_now_like_the_real_bus(ban_bus):
    """``ban(days=1)`` writes a ``banned_until`` one day from UTC NOW, and ``expires_utc`` reads it back.

    The real ``ServiceBus.ban`` computes ``datetime.now(tz=timezone.utc) + timedelta(days=days)`` and
    stores it NAIVE; a stub on LOCAL time reads one day PLUS the host's UTC offset. The row's own
    stamp is the check, not the code path — and ``expires_utc`` (the EDIT dialog's value) must render
    that very stamp, not a re-derived one.
    """
    await ban_bus.ban("u-clock", "mod", "cheating", days=1, server_name="SRS-1")
    row = ban_bus.rows[0]
    assert row["banned_until"].tzinfo is None, "the stub wrote an aware stamp, unlike the real bus"
    expected = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=1)).replace(tzinfo=None)
    assert abs((row["banned_until"] - expected).total_seconds()) < 5, \
        "the stub's day count is not measured from UTC NOW"
    assert ban_expires_utc(row) == row["banned_until"].strftime("%Y-%m-%d %H:%M:%S"), \
        "expires_utc does not render the entry's own end"


# --------------------------------------------------------------- 12. the catalogs carry the words

#: the languages that ship a ``webui`` catalog. English is the source language and the msgid, so it
#: ships none: its strings are the msgids themselves.
LANGUAGES = ("cn", "de", "es", "ru")
DGSA_LABEL = "Show DGSA bans"
DGSA_HIDE_LABEL = "Hide DGSA bans"
DGSA_SHOWN = "DGSA bans shown"
DGSA_PLURAL = ("%(count)s DGSA ban hidden", "%(count)s DGSA bans hidden")


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_webui_catalog_carries_the_dgsa_strings(language):
    """The view option's three msgids are in the source half of every shipped ``webui`` catalog."""
    text = (REPO / "locale" / language / "LC_MESSAGES" / "webui.po").read_text(encoding="utf-8")
    for msgid in (DGSA_LABEL, DGSA_HIDE_LABEL, DGSA_SHOWN):
        assert f'msgid "{msgid}"' in text, f"{language} webui.po lacks {msgid!r}"
    assert f'msgid "{DGSA_PLURAL[0]}"' in text, f"{language} webui.po lacks the plural msgid"
    assert f'msgid_plural "{DGSA_PLURAL[1]}"' in text, f"{language} webui.po lacks msgid_plural"


@pytest.mark.parametrize("language", ("cn", "de", "es", "ru"))
def test_the_compiled_webui_catalog_resolves_the_dgsa_strings(language):
    """The compiled ``webui.mo`` serves them — a stale ``.mo`` cannot carry a fresh ``.po``."""
    translations = gettext.translation("webui", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in (DGSA_LABEL, DGSA_HIDE_LABEL, DGSA_SHOWN):
        assert translations.gettext(msgid) != msgid, f"{language}: {msgid!r} still English"
    assert translations.ngettext(*DGSA_PLURAL, 1) != DGSA_PLURAL[0], f"{language}: plural (1)"
    assert translations.ngettext(*DGSA_PLURAL, 2) != DGSA_PLURAL[1], f"{language}: plural (2)"
