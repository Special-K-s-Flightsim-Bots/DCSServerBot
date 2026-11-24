"""The player page: `/players/<ucid>` — Overview / Infractions / Statistics.

One section per acceptance criterion of the card:

* the ROUTES are registered and gated — every tab answers 200 for a caller holding ``players.view``
  and 403 for one without it, driven through the real application (a template render would prove
  nothing about ``_core_router()``);
* a UCID that is not 32 hexadecimal characters is refused BEFORE any read runs, and a well-formed
  UCID this installation does not know is the console's 404 page, never a 500 and never an empty one;
* the two-entry player (banned AND watched) renders BOTH strips, ban first, and BOTH entries with
  their own two glyphs — and the two records stay two records;
* the Moderation card narrows per BLOCK: without ``bans.view`` no ban block, without ``players.watch``
  a watch entry with no glyphs;
* a clean player gets the quiet strip, no entry block, and still the two UCID-keyed controls;
* the controls split by WHAT THEY NEED: Ban + Add to watchlist for an offline player, plus the four
  SESSION controls only for a player in a slot;
* an unlinked player renders (and says so), and a player with no session data gets one sentence;
* the Statistics tab renders the five figures and the breakdown cards, and an unknown period falls
  back to ``all`` BEFORE the read;
* the tabs are three real links, the one in force carries ``aria-current="page"``;
* the page prints the IP HASH and never a raw address.

The three reads are stubbed at the page's own seam (``pages/player_detail.read_action``): the SQL and
the scope rules belong to the actions, so what this file pins is the PAGE.
"""
from __future__ import annotations

import datetime
import re
import types

import pytest

from core import utils
from services.webservice import permissions
from services.webservice.pages import player_detail as page
from test_webui_write_message import (  # noqa: F401 - the console's write harness
    FakePlayer, build, client, config_dir, server_with_players, sign_in,
)
from test_webui_write_pause import clean_registry  # noqa: F401 - the action registry fixture
from test_webui_watch_actions import audit_bot, store, watch_bus  # noqa: F401 - the watch write's doubles

#: two 32-hex UCIDs — the shape the route accepts — one in a slot and one not
ONLINE_UCID = "4b1f9c22000040008000000000000001"
OFFLINE_UCID = "3e9a70c15d8b42f6a0c3e59b7d14f286"
LINKED_DISCORD = 4711
IPV4 = "203.0.113.42"


def _stamp(year, month, day, hour=12, minute=0):
    return datetime.datetime(year, month, day, hour, minute)


def detail_data(ucid, *, found=True, name="Dirk", discord_id=LINKED_DISCORD, member="viper_dcs",
                ban=None, watch=None, sessions=412, playtime=657660, manual=True):
    """One `get_player_detail` answer, in the action's own shape."""
    return {"ucid": ucid, "found": found,
            "identity": [{"ucid": ucid, "name": name, "manual": manual}] if found else [],
            "name": name, "discord_id": discord_id, "member": member, "vip": False,
            "first_seen": _stamp(2023, 4, 11, 19, 2), "last_seen": _stamp(2026, 10, 30, 11, 47),
            "playtime": playtime, "sessions": sessions, "ban": ban, "watch": watch}


def ban_entry(ucid, *, permanent=False, reason="Teamkilling on purpose", servers=None):
    return {"ucid": ucid, "reason": reason, "banned_by": "dcs.specialk",
            "banned_at": _stamp(2026, 10, 29, 18, 12),
            "banned_until": (datetime.datetime(9999, 12, 31) if permanent
                             else _stamp(2026, 11, 12, 18, 0)),
            "server_name": servers}


def watch_entry(ucid, *, reason="Possible second account of a banned player"):
    return {"ucid": ucid, "reason": reason, "created_by": "dcs.specialk",
            "created_at": _stamp(2026, 10, 29, 22, 40)}


def infractions_data(ucid, *, found=True, penalty_total=9.5, bans=2):
    return {"ucid": ucid, "found": found,
            "infractions": [{"time": _stamp(2026, 10, 24, 20, 38),
                             "infraction": "F/A-18C killed Kolya", "count": 1},
                            {"time": _stamp(2026, 10, 24, 20, 37),
                             "infraction": "F/A-18C hit Sandman", "count": 4}],
            "penalties": [{"time": _stamp(2026, 10, 24, 20, 38), "event": "TEAMKILL",
                           "points": 3},
                          {"time": _stamp(2026, 10, 24, 20, 37), "event": "FRIENDLY_FIRE",
                           "points": 1.5}],
            "penalty_total": penalty_total,
            "ban_history": [{"timeframe": "2026-10-29 - 2026-11-12",
                             "reason": "Teamkilling on purpose", "status": "Active"},
                            {"timeframe": "2025-03-08 - 2025-03-15",
                             "reason": "Teamkilling", "status": "Auto-Unbanned"}]}


def statistics_data(ucid, *, period="all", sessions=412, playtime=657660):
    return {"ucid": ucid, "period": period, "found": True,
            "totals": {"playtime": playtime, "sessions": sessions, "kills": 1204, "ai_kills": 1163,
                       "player_kills": 41, "deaths": 318, "deaths_ai": 214, "deaths_player": 82,
                       "selfkills": 22, "teamkills": 2, "takeoffs": 412, "landings": 396,
                       "ejections": 21, "crashes": 30},
            "kills_by_type": [{"label": " planes", "count": 412},
                              {"label": "helicopters", "count": 210},
                              {"label": "ships", "count": 24},
                              {"label": "sams", "count": 78},
                              {"label": "ground", "count": 480}],
            "per_module": [{"label": "F/A-18C Hornet", "playtime": 304200},
                           {"label": "F-16C Viper", "playtime": 148320}],
            "per_server": [{"label": "DCS Germany #1", "playtime": 437040}],
            "per_map": [{"label": "Caucasus", "playtime": 346320}],
            "recent": [{"hop_on": _stamp(2026, 10, 30, 9, 12), "hop_off": _stamp(2026, 10, 30, 11, 47),
                        "slot": "F/A-18C Hornet", "server_name": "DCS Germany #1",
                        "mission_theatre": "Caucasus", "playtime": 9300}]}


class Reads:
    """The page's read seam: three answers, and the calls the page made."""

    def __init__(self, detail, infractions=None, statistics=None):
        self.detail = detail
        self.infractions = infractions if infractions is not None else infractions_data(detail["ucid"])
        self.statistics = statistics if statistics is not None else statistics_data(detail["ucid"])
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, qualname, ctx, /, **params):
        self.calls.append((qualname, dict(params)))
        if qualname == "get_player_detail":
            return types.SimpleNamespace(success=True, data=self.detail)
        if qualname == "get_player_infractions":
            return types.SimpleNamespace(success=True, data=self.infractions)
        if qualname == "get_player_statistics":
            return types.SimpleNamespace(success=True, data=dict(self.statistics,
                                                                 period=params.get("period") or "all"))
        raise AssertionError(qualname)

    def called(self, qualname):
        return [params for name, params in self.calls if name == qualname]


def plane(name, ucid, *, active=True, ipaddr=IPV4):
    """One player double on a server, with the live fields the activity block reads."""
    player = FakePlayer(name, ucid, active=active)
    setattr(player, "ipaddr", ipaddr)
    setattr(player, "side", types.SimpleNamespace(name="Blue"))
    return player


def fleet(*players, settings=None):
    """One server carrying *players* — the live roster the page's activity block reads."""
    srv = server_with_players("SRS-1", ["Host A"], players=list(players))
    if settings is not None:
        srv.settings = settings
    node = types.SimpleNamespace(name="dcs-a-01", master=True,
                                 instances={"Host-A-Instance": types.SimpleNamespace(
                                     name="Host-A-Instance", dcs_port=10309, webgui_port=8088,
                                     server=srv)})
    from test_webui_write_pause import Cluster, instance

    return Cluster([srv], nodes={"dcs-a-01": node}, node=node,
                   instances=[instance("Host-A-Instance", srv)])


SCREENSHOT_ALLOWED = {"advanced": {"server_can_screenshot": True}}


@pytest.fixture
def read(monkeypatch):
    """Install the detail/infractions/statistics stubs and hand the test the double."""
    stub = Reads(detail_data(ONLINE_UCID))
    monkeypatch.setattr(page, "read_action", stub)
    return stub


def install(stub, monkeypatch):
    monkeypatch.setattr(page, "read_action", stub)
    return stub


async def open_page(http, path, expect=200):
    response = await http.get(path)
    assert response.status_code == expect, response.text
    return response.text


def section(html: str, marker: str) -> str:
    """The substring from *marker* to the end — for ``first…then`` order assertions."""
    assert marker in html, f"the page does not carry {marker!r}"
    return html[html.index(marker):]


def forms_for(html: str, path: str) -> list[str]:
    return re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)


# ---------------------------------------------------------- 1. the routes, the gate

@pytest.mark.asyncio
@pytest.mark.parametrize("path", [page.PLAYER_PATH, page.PLAYER_INFRACTIONS_PATH,
                                  page.PLAYER_STATISTICS_PATH])
async def test_every_tab_answers_200_for_a_role_holding_players_view(config_dir, read, path):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(path.format(ucid=ONLINE_UCID))
    assert response.status_code == 200, response.text
    assert "Dirk" in response.text


@pytest.mark.asyncio
async def test_the_page_answers_403_for_a_role_without_players_view(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "plain")
        response = await http.get(page.PLAYER_PATH.format(ucid=ONLINE_UCID))
    assert response.status_code == 403, response.text


# ------------------------------------------- 2. the UCID's shape, and the unknown player

@pytest.mark.asyncio
@pytest.mark.parametrize("ucid", ["abc", "z" * 32, "%20" * 32, "3e9a70c15d8b42f6a0c3e59b7d14f28"])
async def test_a_malformed_ucid_is_refused_before_any_read_runs(config_dir, read, ucid):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(page.PLAYER_PATH.format(ucid=ucid))
    assert response.status_code == 404, response.text
    assert read.calls == [], "a malformed UCID must not reach a read"


@pytest.mark.asyncio
async def test_an_unknown_but_well_formed_ucid_renders_the_404_page(config_dir, read, monkeypatch):
    stub = Reads(detail_data(ONLINE_UCID, found=False))
    stub.infractions = infractions_data(ONLINE_UCID, found=False)
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        # a BROWSER's refusal (the console's page face), not an API caller's JSON
        response = await http.get(page.PLAYER_PATH.format(ucid="0" * 32),
                                  headers={"accept": "text/html"})
    assert response.status_code == 404, response.text
    assert "No such player" in response.text, "a well-formed 404 page, not a blank body"
    assert "No player on this installation carries this UCID" in response.text
    assert 'href="/players"' in response.text, "the page offers the way back"
    assert len(stub.called("get_player_detail")) == 1


# --------------------------------------------- 3. the two records, never one state

@pytest.mark.asyncio
async def test_a_banned_and_watched_player_renders_both_strips_ban_first(config_dir, monkeypatch):
    stub = Reads(detail_data(ONLINE_UCID, ban=ban_entry(ONLINE_UCID),
                             watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    assert 'class="player-state banned"' in html and 'class="player-state watch"' in html
    assert html.index('class="player-state banned"') < html.index('class="player-state watch"'), \
        "the ban strip is the harder restriction and comes first"
    assert 'class="player-state clean"' not in html


@pytest.mark.asyncio
async def test_each_entry_carries_its_own_two_glyphs(config_dir, monkeypatch):
    stub = Reads(detail_data(ONLINE_UCID, ban=ban_entry(ONLINE_UCID),
                             watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    from services.webservice.pages import actions as actions_page

    ban_block = section(html, 'class="player-entry ban"')
    ban_block = ban_block[:ban_block.index("player-entry watch")]
    watch_block = section(html, 'class="player-entry watch"')

    assert forms_for(ban_block, actions_page.BAN_LIFT_PATH + actions_page.CONFIRM_SUFFIX), \
        "the ban entry offers the LIFT"
    assert forms_for(ban_block, actions_page.BAN_EDIT_PATH + actions_page.CONFIRM_SUFFIX), \
        "the ban entry offers the EDIT"
    assert forms_for(watch_block, actions_page.WATCH_REMOVE_PATH + actions_page.CONFIRM_SUFFIX), \
        "the watch entry offers the REMOVE"
    assert forms_for(watch_block, actions_page.WATCH_EDIT_PATH + actions_page.CONFIRM_SUFFIX), \
        "the watch entry offers the EDIT"
    assert ban_block.count('class="ic24"') == 2, "one glyph per ban control, and no more"
    assert watch_block.count('class="ic24"') == 2, "one glyph per watch control, and no more"
    assert "Lift the ban on Dirk" in ban_block
    assert "Remove Dirk from the watchlist" in watch_block


@pytest.mark.asyncio
async def test_the_page_states_that_lifting_one_leaves_the_other(config_dir, monkeypatch):
    stub = Reads(detail_data(ONLINE_UCID, ban=ban_entry(ONLINE_UCID),
                             watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))
    assert "Lifting one leaves the other untouched" in html


# ------------------------------------------------------- 4. the card's per-block capability

@pytest.mark.asyncio
async def test_the_ban_block_is_absent_without_bans_view(config_dir, monkeypatch):
    stub = Reads(detail_data(ONLINE_UCID, ban=ban_entry(ONLINE_UCID),
                             watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    real = permissions.allows
    monkeypatch.setattr(permissions, "allows",
                        lambda capability, roles, *, manager=False: (
                            False if capability == "bans.view"
                            else real(capability, roles, manager=manager)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    assert 'class="player-entry ban"' not in html, "no bans.view, no ban block"
    assert 'class="player-state banned"' in html, "the strip is the page's own state, not the block"
    assert 'class="player-entry watch"' in html


def _capability_calls(monkeypatch, denied: str = "", asked: list | None = None) -> list[str]:
    """Deny ONE capability (``""`` denies nothing) and record every capability the page asked about.

    The page resolves its two block flags through ``permissions.allows``; recording the names is how
    a pin shows WHICH predicate withheld a block rather than only that something was absent.
    """
    seen: list[str] = [] if asked is None else asked
    original = permissions.allows

    def allows(capability, roles, *, manager=False):
        seen.append(capability)
        if capability == denied:
            return False
        return original(capability, roles, manager=manager)

    monkeypatch.setattr(permissions, "allows", allows)
    return seen


@pytest.mark.asyncio
async def test_the_watch_facts_are_absent_without_players_watch(config_dir, monkeypatch):
    """``players.view`` without the watch capability reads NO watch fact at all.

    The contract MOVED here: the page used to keep the entry's facts and drop only its glyphs, which
    let the player page become a way around the watchlist's own scope. Reason, author and the
    ``Watched`` strip are now all withheld, and no quiet strip takes their place.
    """
    stub = Reads(detail_data(ONLINE_UCID, watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    asked = _capability_calls(monkeypatch, denied="players.watch")
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    assert "players.watch" in asked, "the view must consult the watch capability itself"
    assert 'class="player-entry watch"' not in html, "no watch entry"
    assert "Possible second account of a banned player" not in html, "no watch reason"
    assert "dcs.specialk" not in html, "no created_by"
    assert "1 watch entry" not in html, "no watch tag on the card"
    assert 'class="player-state watch"' not in html, "no Watched strip"
    assert ">Watched<" not in html
    assert 'class="player-state clean"' not in html, \
        "a withheld watch takes no quiet strip: the page must not claim the player is unwatched"


@pytest.mark.asyncio
async def test_the_watch_facts_are_rendered_with_players_watch(config_dir, monkeypatch):
    """The same page for a caller holding the capability renders the entry, its author and the strip."""
    stub = Reads(detail_data(ONLINE_UCID, watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    asked = _capability_calls(monkeypatch)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    assert "players.watch" in asked
    watch_block = section(html, 'class="player-entry watch"')
    assert "Possible second account of a banned player" in watch_block
    assert "dcs.specialk" in watch_block
    assert 'class="player-state watch"' in html and ">Watched<" in html


@pytest.mark.asyncio
async def test_a_ban_survives_while_the_watch_block_is_withheld(config_dir, monkeypatch):
    """A ``bans.view``-only caller keeps the ban block and reads nothing about the watch entry."""
    stub = Reads(detail_data(ONLINE_UCID, ban=ban_entry(ONLINE_UCID),
                             watch=watch_entry(ONLINE_UCID)))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    _capability_calls(monkeypatch, denied="players.watch")
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    ban_block = section(html, 'class="player-entry ban"')
    assert "Teamkilling on purpose" in ban_block, "the ban facts stay for a bans.view holder"
    assert 'class="player-entry watch"' not in html
    assert "Possible second account of a banned player" not in html
    assert 'class="player-state banned"' in html and 'class="player-state watch"' not in html


# --------------------------------------------------------------- 5. the clean player

@pytest.mark.asyncio
async def test_a_clean_player_gets_the_quiet_strip_and_no_entry(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    from services.webservice.pages import actions as actions_page

    assert 'class="player-state clean"' in html
    assert "No ban" in html and "and not on the watchlist" in html
    assert 'class="player-entry ban"' not in html and 'class="player-entry watch"' not in html
    assert forms_for(html, actions_page.BAN_ADD_PATH + actions_page.CONFIRM_SUFFIX)
    assert forms_for(html, actions_page.WATCH_ADD_PATH + actions_page.CONFIRM_SUFFIX)


# ------------------------------------------------- 6. the controls' split by requirement

@pytest.mark.asyncio
async def test_an_offline_player_is_offered_the_ucid_pair_and_none_of_the_session_four(
        config_dir, monkeypatch):
    stub = Reads(detail_data(OFFLINE_UCID))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", OFFLINE_UCID, active=False)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=OFFLINE_UCID))

    from services.webservice.pages import actions as actions_page

    head = section(html, 'class="page-actions"')
    head = head[:head.index("</div>", head.index("</form>")) + 200]
    assert ">Ban</button>" in head and ">Add to watchlist</button>" in head
    for glyph in ("chat", "popup", "kick", "camera"):
        assert forms_for(head, {"chat": actions_page.CHAT_PATH,
                                "popup": actions_page.POPUP_PATH,
                                "kick": "/actions/player/kick",
                                "camera": actions_page.SCREENSHOT_PATH}[glyph]) == []
    assert "Not on a server right now." in html


@pytest.mark.asyncio
async def test_a_player_in_a_slot_is_offered_all_four_session_controls(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID), settings=SCREENSHOT_ALLOWED),
                plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    from services.webservice.pages import actions as actions_page

    for label in ("Message", "Popup", "Kick", "Screenshot"):
        assert f">{label}</button>" in html, f"the session control {label} is missing"
    assert forms_for(html, actions_page.CHAT_PATH + actions_page.CONFIRM_SUFFIX)
    assert forms_for(html, actions_page.POPUP_PATH + actions_page.CONFIRM_SUFFIX)
    assert forms_for(html, "/actions/player/kick" + actions_page.CONFIRM_SUFFIX)
    # the screenshot has no dialog: its own route captures and the page opens the image
    assert forms_for(html, actions_page.SCREENSHOT_PATH)
    assert "Not on a server right now." not in html


# ------------------------------------------------------- 7. the unlinked, the no-data player

@pytest.mark.asyncio
async def test_an_unlinked_player_renders_and_says_so(config_dir, monkeypatch):
    stub = Reads(detail_data(OFFLINE_UCID, discord_id=-1, member=""))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", OFFLINE_UCID, active=False)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=OFFLINE_UCID))

    assert "not linked to a Discord member" in html
    assert "No Discord member is linked to this UCID" in html


@pytest.mark.asyncio
async def test_the_statistics_tab_of_a_player_without_sessions_is_one_sentence(
        config_dir, monkeypatch):
    empty = statistics_data(ONLINE_UCID, sessions=0)
    empty["totals"]["playtime"] = None
    stub = Reads(detail_data(ONLINE_UCID), statistics=empty)
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_STATISTICS_PATH.format(ucid=ONLINE_UCID))

    assert "No session has been recorded for this player" in html
    assert 'class="player-figs"' not in html, "no empty chart frame"


# --------------------------------------------------------------- 8. the statistics tab

@pytest.mark.asyncio
async def test_the_statistics_tab_renders_the_figures_and_the_breakdowns(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_STATISTICS_PATH.format(ucid=ONLINE_UCID))

    assert 'class="player-figs"' in html
    for word in ("Playtime", "Kills", "Deaths", "K/D", "Teamkills"):
        assert f'<div class="k">{word}</div>' in html, word
    for heading in ("Playtime per module", "Playtime per server", "Playtime per map",
                    "Flight performance", "Kills by target type", "Deaths by cause",
                    "Recent sessions"):
        assert heading in html, heading
    assert "1,204" not in html and ">1204<" in html  # one spelling of a figure
    assert 'style="width:100%"' in html, "the longest bar is full"


@pytest.mark.asyncio
async def test_an_unknown_period_falls_back_to_all_before_the_read(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http,
                              page.PLAYER_STATISTICS_PATH.format(ucid=ONLINE_UCID) + "?period=garbage")

    assert read.called("get_player_statistics")[-1]["period"] == "all"
    assert 'href="/players/' + ONLINE_UCID + '/statistics?period=all" aria-current="true"' in html


@pytest.mark.asyncio
async def test_the_page_renders_in_the_readers_language(config_dir, read):
    """The page's copy is translated at render, through the request's own language."""
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(page.PLAYER_PATH.format(ucid=ONLINE_UCID),
                                  headers={"accept-language": "de"})

    assert response.status_code == 200, response.text
    assert "Identit\u00e4t &amp; Verkn\u00fcpfung" in response.text
    assert "Strafpunkte" in response.text


def test_period_view_is_a_closed_vocabulary():
    assert page.period_view("garbage") == "all"
    assert page.period_view(None) == "all"
    assert page.period_view("MONTH") == "month"


# --------------------------------------------------------------------- 9. the tabs

@pytest.mark.asyncio
async def test_the_tabs_are_three_links_and_the_active_one_is_marked(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_INFRACTIONS_PATH.format(ucid=ONLINE_UCID))

    for suffix in ("", "/infractions", "/statistics"):
        assert f'href="/players/{ONLINE_UCID}{suffix}"' in html, suffix
    assert re.search(rf'<a class="tab on" href="/players/{ONLINE_UCID}/infractions" '
                     r'aria-current="page">Infractions</a>', html)


# ------------------------------------------------------------------- 10. the IP hash

@pytest.mark.asyncio
async def test_the_page_prints_the_ip_hash_and_never_the_address(config_dir, read):
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=ONLINE_UCID))

    digest = utils.hash_ip_addr(IPV4)
    assert digest in html, "the hash of the player's address is the page's only address fact"
    assert IPV4 not in html, "the raw address must never reach a web page"
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", html), "no IPv4 literal anywhere"


@pytest.mark.asyncio
async def test_an_offline_player_has_no_ip_hash_to_print(config_dir, monkeypatch):
    stub = Reads(detail_data(OFFLINE_UCID))
    install(stub, monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", OFFLINE_UCID, active=False)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, page.PLAYER_PATH.format(ucid=OFFLINE_UCID))

    assert utils.hash_ip_addr(IPV4) not in html


# ---------------------------------------- 11. the write's outcome is read on the DIRECTORY

#: One page of the directory read, empty: this section is about where a write RETURNS, not about rows.
EMPTY_DIRECTORY = {"rows": [], "total": 0, "page": 1, "pages": 1, "per_page": 50, "query": ""}


def directory_read_stub(monkeypatch):
    """Point the Players page's own read seam at an empty directory."""
    from services.webservice.pages import players as players_page

    async def _read(qualname, ctx, /, **params):
        assert qualname == "list_player_directory", qualname
        return types.SimpleNamespace(success=True, data=EMPTY_DIRECTORY)

    monkeypatch.setattr(players_page, "read_action", _read)


@pytest.mark.asyncio
async def test_a_write_started_on_this_page_lands_on_the_directory_with_its_notice(
        config_dir, clean_registry, store, watch_bus, audit_bot, monkeypatch):
    """This page's controls carry ``WRITE_ORIGIN``, so the outcome is read where they land: once."""
    from services.webservice.pages import actions as actions_page
    from services.webservice.pages import players as players_page
    from test_webui_watch_actions import csrf, notice

    assert page.WRITE_ORIGIN == players_page.PLAYERS_PATH, \
        "every control on this page returns to the directory"
    directory_read_stub(monkeypatch)
    app = build(config_dir, fleet(plane("Dirk", ONLINE_UCID)), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = await csrf(http)
        reply = await http.post(actions_page.WATCH_ADD_PATH,
                                data={"ucid": ONLINE_UCID, "reason": "metagaming",
                                      actions_page.ORIGIN_FIELD: page.WRITE_ORIGIN,
                                      "_csrf_token": token})
        assert reply.status_code == 303, (reply.status_code, reply.text[:200])
        assert reply.headers["location"] == page.WRITE_ORIGIN, reply.headers.get("location")
        landed = await http.get(page.WRITE_ORIGIN)
        again = await http.get(page.WRITE_ORIGIN)

    assert ONLINE_UCID in store.watch, "the write landed"
    assert landed.status_code == 200, landed.text[:300]
    assert 'class="notice ok"' in landed.text, "the landing page states the write's outcome"
    assert ONLINE_UCID in notice(landed.text)
    assert notice(again.text) == "", "the notice is one-shot: a second render carries none"
