"""The public site's "my statistics" and the sign-in path it opens.

WHAT THIS SUITE PINS
--------------------
* a signed-out visitor gets the page — with the sign-in control and no figure — and never a
  redirect: a shared link or a search engine must not land a stranger on a login form;
* a signed-in visitor's figures are ``site_reads.my_statistics``' answer, run once, for their own
  Discord id and only over this site's servers, under the "this is you" band;
* a viewer with no record here — not a member of this site, or never played on it — still gets the
  page over this site's servers, never a refusal;
* the return target after sign-in is confined to the site prefix that started it: ``/console``,
  another site's prefix and an absolute URL each resolve to the site root, a relative in-site path
  survives;
* the chip offers the console only to a visitor whose roles (or manager scope) reach it;
* one session serves every site of the host — the render sets no narrower cookie than ``/``.

The browser measurements (1440/430, scroll width, page heights) are reported on the card; they are
not re-run here.
"""
from __future__ import annotations

import asyncio
import datetime
import re
import types

import pytest

from conftest import asgi_client
from services.webservice import permissions, site_pages, sites
from services.webservice.auth import Identity
from test_webui_site_pages import build, bus, config_dir, get, site_config  # noqa: F401 - fixtures

#: the Discord id the signed-in identity carries. A SENTINEL: it must never reach the page.
DISCORD_ID = 111_222_333_444_555_666

#: the pilots one Discord link owns. Two UCIDs, so the "several player IDs" branch is exercised;
#: every figure is a sentinel in a range no other number on the page uses.
PILOTS: dict[str, dict] = {
    "u-me-1": {"name": "Viper One", "server": "Group-1", "playtime": 111 * 3600, "sessions": 1111,
               "kills": 1113, "pvp": 1115, "deaths": 1117, "takeoffs": 1119,
               "aircraft": [{"aircraft": "F-16C_50", "playtime": 100 * 3600, "sessions": 900}],
               "recent": [{"hop_on": "2026-11-06T21:12:00+00:00"}]},
    "u-me-2": {"name": "Viper Two", "server": "Group-1", "playtime": 11 * 3600, "sessions": 12,
               "kills": 0, "pvp": 0, "deaths": 0, "takeoffs": 0, "aircraft": [], "recent": []},
    "u-root": {"name": "Root Pilot", "server": "Root-1", "playtime": 7 * 3600, "sessions": 7,
               "kills": 0, "pvp": 0, "deaths": 0, "takeoffs": 0, "aircraft": [], "recent": []},
}

#: the UCIDs the Discord link owns in the stub database, per site.
LINKED: dict[str, tuple[str, ...]] = {"group1": ("u-me-1", "u-me-2"), "": ("u-root",)}


class _Cursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, params))
        self._rows = iter(self._pool.answer(statement, params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None


class _Connection:
    def __init__(self, pool):
        self._pool = pool

    def cursor(self, **_kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class StubPool:
    """The viewer's own record, bound to the site's servers — the read's own shape.

    ``tables`` decides which plugin tables exist (the rating's and the campaign's panels vanish
    without them), ``rating``/``history`` are the competitive plugin's rows and ``campaigns`` the
    credits-and-badge answer.
    """

    def __init__(self, *, tables=(), rating=None, history=None, campaigns=None, events=None,
                 kills=None, starts=None):
        self.calls: list[tuple[str, dict]] = []
        self.linked: dict[str, tuple[str, ...]] = dict(LINKED)
        self.tables = set(tables)
        self.rating = rating
        self.history = list(history or [])
        self.campaigns = list(campaigns or [])
        self.events = list(events or [])
        self.kills = list(kills or [])
        self.starts = list(starts or [])

    def connection(self):
        return _Connection(self)

    def answer(self, statement, params):
        params = params or {}
        if "to_regclass" in statement:
            return [{"present": params.get("name") in self.tables}]
        # the progress read's two statements, both over `missionstats`: the kill one first, since
        # it shares the table name with the sortie one
        if "ms.event = 'S_EVENT_KILL'" in statement:
            return list(self.kills)
        if "ms.event = ANY" in statement:
            return list(self.events)
        # the strip's flight count: every takeoff and every air start, one row per UCID
        if "air_starts" in statement:
            return list(self.starts)
        if "FROM campaigns c" in statement:
            return list(self.campaigns)
        if "FROM trueskill_hist" in statement:
            rows = [*self.history, *([self.rating] if self.rating else [])]
            return sorted(rows, key=lambda row: str(row.get("time") or ""))
        if "FROM trueskill" in statement:
            return [self.rating] if self.rating else []
        names = list(params.get("servers") or ())
        if "WHERE p.discord_id = %(discord_id)s" in statement:
            key = "group1" if "Group-1" in names else ""
            return [{"ucid": ucid, "name": PILOTS[ucid]["name"]}
                    for ucid in self.linked.get(key, ())
                    if PILOTS[ucid]["server"] in names]
        ucid = (params.get("ucids") or [""])[0]
        pilot = PILOTS.get(ucid, {})
        if "AS kills" in statement:
            return [{key: pilot.get(key, 0) for key in
                     ("playtime", "sessions", "kills", "pvp", "deaths", "teamkills", "takeoffs",
                      "landings", "crashes", "ejections")}]
        if "s.slot AS aircraft" in statement:
            return list(pilot.get("aircraft") or [])
        if "m.server_name AS server_name" in statement:
            return ([{"server_name": pilot["server"], "playtime": pilot.get("playtime", 0),
                      "sessions": pilot.get("sessions", 0)}] if pilot else [])
        if "SELECT s.hop_on, s.hop_off" in statement:
            return list(pilot.get("recent") or [])
        return []


@pytest.fixture
def pool(bus):
    bus.apool = StubPool()
    return bus.apool


class StubBackend:
    """A Discord backend stub: the start route redirects through it, nothing else happens."""

    def authorize_redirect(self, state):
        return f"https://discord.example/oauth2/authorize?state={state}"


class StubAuth:
    """An identity layer with the doors a deployment declares — nothing else."""

    def __init__(self, identity=None, *, discord: bool = True, password: bool = False):
        self.identity = identity
        self._discord = discord
        self._password = password
        self.backend_stub = StubBackend()

    def authenticate(self, request):
        return self.identity

    def backend(self, name):
        return self.backend_stub if (self._discord and name == "discord") else None

    @property
    def password_backends(self):
        return (object(),) if self._password else ()


@pytest.fixture(autouse=True)
def clean_permissions():
    """Give every test a known role/manager resolution and restore the installed ones after."""
    yield
    permissions.set_role_resolver(None)
    permissions.set_scope_resolver(None)
    permissions.set_manager_resolver(None)


def as_viewer(app, who=None, *, roles=(), manager: bool = False, discord: bool = True,
              password: bool = False):
    """Hand *app* an identity layer: *who* signed in, with *roles* and the manager flag."""
    app.state.webui_auth = StubAuth(who, discord=discord, password=password)
    permissions.set_role_resolver(lambda request: set(roles))
    permissions.set_manager_resolver(lambda request: manager)
    return app


def viewer(*, roles=(), display_name="Viper One") -> Identity:
    return Identity(subject=str(DISCORD_ID), backend="discord", roles=frozenset(roles),
                    display_name=display_name)


def fetch(app, path):
    """One GET against the real application, with the response headers the browser would see."""
    async def run():
        async with asgi_client(app) as client:
            response = await client.get(path)
            return response, client

    return asyncio.run(run())


# ------------------------------------------------------------------------------- signed out

def test_a_signed_out_visitor_gets_the_page_and_no_redirect(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None)
    response, _ = fetch(app, "/group1/me")
    assert response.status_code == 200, "a GET of the page must never redirect a stranger"
    markup = response.text
    assert 'class="strip' not in markup, "the signed-out page renders figures"
    assert "This is you" not in markup, "the signed-out page claims to be somebody's record"
    # the asset version in a script/stylesheet URL is a timestamp and can contain the sentinel by
    # coincidence; the figures themselves are what this pin is about, so the version tokens are
    # dropped before the search.
    assert "111" not in re.sub(r"\?v=\d+", "", markup)
    assert not pool.calls, "the signed-out page ran a statement"


def test_the_signed_out_page_offers_the_sign_in_control_and_nothing_else(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None)
    markup = fetch(app, "/group1/me")[0].text
    assert 'class="btn btn-solid" href="/auth/discord?next=/group1"' in markup
    main = markup.split("<main", 1)[1]
    assert main.count("<a ") == 1, "the signed-out page offers more than the door"


def test_a_discord_only_installation_signs_in_through_the_existing_flow(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None, discord=True, password=False)
    markup = fetch(app, "/me")[0].text
    assert 'href="/auth/discord?next=/"' in markup
    assert "/auth/login" not in markup


def test_an_installation_with_both_doors_lands_on_the_login_page(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None, discord=True, password=True)
    markup = fetch(app, "/group1/me")[0].text
    assert 'href="/auth/login?next=/group1&amp;surface=/group1"' in markup
    assert "/auth/discord" not in markup


def test_an_installation_with_no_door_offers_none(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None, discord=False, password=False)
    response, _ = fetch(app, "/group1/me")
    assert response.status_code == 200
    assert "/auth/" not in response.text
    assert "Signing in is not available on this site." in response.text


def test_the_sign_in_control_walks_the_existing_start_route_into_this_site(config_dir, pool,
                                                                         monkeypatch):
    """No new callback route: the control's own ``next`` is what the OAuth start carries."""
    from services.webservice.auth import discord_oauth

    monkeypatch.setattr(discord_oauth, "_backend", lambda request: StubBackend())
    app = as_viewer(build(config_dir)[0], None)
    markup = fetch(app, "/group1/me")[0].text
    href = markup.split('class="btn btn-solid" href="', 1)[1].split('"', 1)[0]
    response, _ = fetch(app, href)
    assert response.status_code == 302, "the existing start route did not take the door"
    assert response.headers["location"].startswith("https://discord.example/oauth2/authorize")
    assert href == "/auth/discord?next=/group1"


# ------------------------------------------------------------------- the return target

@pytest.mark.parametrize("target,expected", [
    ("/console", "/group1"),
    ("/console/bans", "/group1"),
    ("/", "/group1"),
    ("https://evil.example/x", "/group1"),
    ("//evil.example/x", "/group1"),
    ("/group1abc", "/group1"),
    ("/group1/rankings?metric=playtime", "/group1/rankings?metric=playtime"),
    ("/group1/me", "/group1/me"),
])
def test_a_return_target_is_confined_to_the_site_that_started_it(config_dir, pool, target,
                                                                 expected):
    app = as_viewer(build(config_dir)[0], None)
    response, _ = fetch(app, f"/group1/me?next={target}")
    assert response.status_code == 200
    assert _sign_in_target(response.text) == expected


def _sign_in_target(markup: str) -> str:
    """The ``next`` the PAGE's own sign-in control carries, with HTML entities undone.

    Read from the content BELOW the shell's ``</header>``: the top bar carries the shell's own
    sign-in control too, so the first ``/auth/`` href in the document is not this page's.
    """
    from urllib.parse import unquote

    content = markup.split("</header>", 1)[-1]
    href = content.split('href="/auth/', 1)[1].split('"', 1)[0]
    target = href.split("next=", 1)[1].split("&", 1)[0]
    return unquote(target.replace("&amp;", "&"))


@pytest.mark.parametrize("target", ["/console", "/group1/rankings", "/console/players",
                                    "https://evil.example/"])
def test_the_root_site_refuses_the_console_and_every_group(config_dir, pool, target):
    app = as_viewer(build(config_dir)[0], None)
    markup = fetch(app, f"/me?next={target}")[0].text
    assert _sign_in_target(markup) == "/"


def test_safe_return_is_the_one_rule(config_dir, pool):
    app = as_viewer(build(config_dir)[0], None)
    resolved = sites.sites_for(app).resolve("group1")
    with_request = types.SimpleNamespace(app=app)
    assert site_pages.safe_return(with_request, resolved, "/console/x") == "/group1"
    assert site_pages.safe_return(with_request, resolved, "/group1/players") == "/group1/players"
    assert site_pages.safe_return(with_request, resolved, "") == "/group1"
    assert site_pages.safe_return(with_request, resolved, None) == "/group1"
    assert site_pages.safe_return(with_request, resolved, "/group1\n/elsewhere") == "/group1/elsewhere"


# ------------------------------------------------------------------------------- signed in

def test_a_signed_in_viewer_sees_the_band_and_their_own_figures(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    response, _ = fetch(app, "/group1/me")
    markup = response.text
    assert response.status_code == 200
    assert "This is you" in markup, "the band that says whose figures these are is missing"
    assert "Viper One" in markup
    assert "111" in markup, "the playtime figure did not reach the page"
    assert 'href="/group1/player/u-me-1"' in markup
    assert "Root Pilot" not in markup, "another site's pilot reached this page"


def test_the_read_runs_once_for_the_viewers_own_discord_id(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    fetch(app, "/group1/me")
    statements = [(statement, params) for statement, params in pool.calls
                  if "WHERE p.discord_id = %(discord_id)s" in statement]
    assert len(statements) == 1, "the viewer's link was read more than once"
    assert statements[0][1]["discord_id"] == DISCORD_ID
    assert statements[0][1]["servers"] == ["Group-1"], "the read was not scoped to this site"


def test_a_second_ucid_is_counted_but_not_listed(config_dir, pool):
    """A second linked UCID still feeds the figures; the page no longer lists or advertises one.

    The record is named after the link's FIRST UCID and its "open my public profile" button opens
    that pilot — a second UCID joins the totals but is not a link the visitor browses from here.
    """
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Viper One" in markup, "the record is not named after the link's first player ID"
    assert 'href="/group1/player/u-me-1"' in markup, "the public-profile link is missing"
    assert "player IDs linked" not in markup, "the page still advertises how many IDs are linked"
    assert "Viper Two" not in markup, "a second player ID is listed on the page again"


def test_a_viewer_with_no_record_here_still_gets_the_page(config_dir, pool):
    pool.linked = {}
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    response, _ = fetch(app, "/group1/me")
    assert response.status_code == 200, "a viewer with no record must not be refused"
    assert "No record on these servers" in response.text
    assert 'class="strip' not in response.text
    assert str(DISCORD_ID) not in response.text


def test_a_viewer_of_another_site_is_not_refused_here(config_dir, pool):
    """The identity's pilot plays on the root site only: this site says so, and does not 403."""
    pool.linked = {**LINKED, "group1": ("u-root",)}
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    response, _ = fetch(app, "/group1/me")
    markup = response.text
    assert response.status_code == 200, "a viewer of another site must not be refused here"
    assert "No record on these servers" in markup
    assert "Root Pilot" not in markup, "another site's pilot reached this page"
    assert sites.sites_for(app).resolve("group1").servers == frozenset({"Group-1"})


# ------------------------------------------------------------------------------- the chip

def test_the_console_link_appears_only_for_roles_that_reach_it(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    header = fetch(app, "/group1/me")[0].text.split("</header>", 1)[0]
    assert "Command Center" not in header, "a member without a console role is offered the console"

    app = as_viewer(build(config_dir)[0], viewer(roles={"Admin"}))
    header = fetch(app, "/group1/me")[0].text.split("</header>", 1)[0]
    assert 'href="/console/"' in header and "Command Center" in header


def test_a_manager_reaches_the_console_without_a_role(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}), manager=True)
    header = fetch(app, "/group1/me")[0].text.split("</header>", 1)[0]
    assert 'href="/console/"' in header


def test_the_chip_links_to_the_sites_own_me_page(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    header = fetch(app, "/group1/me")[0].text.split("</header>", 1)[0]
    assert 'href="/group1/me"' in header
    assert "Viper One" in header


# ------------------------------------------------------------------------------- the session

def test_one_session_serves_every_site_of_the_host(config_dir, pool):
    """Nothing in this page narrows the cookie: it is written for the whole host, once."""
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))

    async def run():
        async with asgi_client(app) as client:
            first = await client.get("/group1/me")
            second = await client.get("/me")
            return first, second, client.cookies

    first, second, cookies = asyncio.run(run())
    assert first.status_code == 200 and second.status_code == 200
    cookie = first.headers.get("set-cookie", "")
    assert cookie, "the render carried no session cookie to inspect"
    assert "path=/" in cookie.lower() and "path=/group1" not in cookie.lower()
    assert all(entry.path == "/" for entry in cookies.jar), [entry.path for entry in cookies.jar]
    assert "This is you" in second.text, "the second site did not see the same session"


def test_the_site_writes_only_the_shared_language_cookie(config_dir, pool):
    """The host's one session plus ONE shared preference: no site module writes a cookie of its own.

    The language switch is the only write, and it must be the console's own ``dcssb_lang`` so the
    public site and the Command Center cannot drift onto two cookies.
    """
    import inspect

    from services.webservice.pages import site_leaderboard, site_me, site_overview, site_player
    from services.webservice.pages import site_players

    sources = [inspect.getsource(site_pages)] + [inspect.getsource(module) for module in
                                                 (site_overview, site_leaderboard, site_player,
                                                  site_players, site_me)]
    for source in sources:
        assert "delete_cookie" not in source
        writes = [line for line in source.splitlines() if "set_cookie" in line]
        assert len(writes) <= 1, "a site module writes more than the one shared language cookie"
        for line in writes:
            assert "i18n.COOKIE_NAME" in line, "a site module writes a cookie that is not dcssb_lang"


def test_the_sign_out_control_posts_to_the_logout_route(config_dir, pool):
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert 'method="post" action="/auth/logout"' in markup
    assert 'name="_csrf_token"' in markup


# ------------------------------------------------------------------------------- i18n, wiring

def test_the_page_renders_in_the_sites_own_language(config_dir, pool):
    config = site_config()
    config["sites"]["group1"]["lang"] = "de"
    app = as_viewer(build(config_dir, config=config)[0], None)
    markup = fetch(app, "/group1/me")[0].text
    assert "Meine Statistik" in markup
    assert "Melde dich an, um deinen eigenen Eintrag zu sehen" in markup


def test_the_me_page_is_registered_for_every_site(config_dir, pool):
    _app, registrar = build(config_dir)
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    assert "/me" in owned and "/group1/me" in owned


def test_the_view_is_pure_and_names_the_viewers_own_record(config_dir, pool):
    from services.webservice.pages import site_me

    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    resolved = sites.sites_for(app).resolve("group1")
    model = site_me.view(resolved, viewer(), {
        "found": True,
        "totals": {"playtime": 111 * 3600, "sessions": 1111, "kills": 1113, "pvp": 1115,
                   "deaths": 1117, "takeoffs": 1119},
        "per_aircraft": [{"aircraft": "F-16C_50", "playtime": 100 * 3600, "sessions": 900}],
        "per_server": [{"server_name": "Group-1", "playtime": 111 * 3600, "sessions": 1111}],
        "ucids": [{"ucid": "u-me-1", "name": "Viper One"}],
        "recent": [{"hop_on": "2026-11-06T21:12:00+00:00"}],
    })
    assert model["signed_in"] is True and model["found"] is True
    assert model["name"] == "Viper One"
    assert model["last_flown"] == "2026-11-06"
    assert [row["value"] for row in model["readouts"]] == ["111", "1,111", "1,119", "1,113",
                                                           "1.00"]
    assert model["public_href"] == "/group1/player/u-me-1"


# ------------------------------------------- the panels only the pilot's own page carries

RATING = {"skill_mu": 43.7, "skill_sigma": 4.1, "time": "2026-11-01T20:00:00"}
HISTORY = [{"skill_mu": 40.0, "skill_sigma": 6.2, "time": "2025-05-04T20:12:00"},
           {"skill_mu": 42.0, "skill_sigma": 5.0, "time": "2026-04-02T19:05:00"}]

CAMPAIGNS = [
    {"name": "Syria 82", "start": datetime.datetime(2026, 1, 10, 12, 0),
     "stop": None, "points": 1234, "badge_name": "Veteran",
     "badge_url": "https://example.com/veteran.png"},
    {"name": "Kola Cold War", "start": datetime.datetime(2025, 3, 1, 12, 0),
     "stop": datetime.datetime(2025, 6, 1, 12, 0), "points": 87, "badge_name": None,
     "badge_url": None},
]


def test_the_rating_history_is_drawn_on_the_pilots_own_page(config_dir, bus):
    """The split: the public profile states the number, the pilot's page draws how it got there."""
    bus.apool = StubPool(tables=("trueskill", "trueskill_hist"), rating=RATING, history=HISTORY)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Competitive rating" in markup
    assert "43.7" in markup
    assert 'role="img"' in markup, "the chart's one accessibility sentence is missing"
    assert '<path class="rc-band"' in markup and '<polyline class="rc-line"' in markup
    assert "Up 3.7 points across this span." in markup


def test_the_rating_panel_is_absent_without_the_plugin_tables(config_dir, bus):
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Competitive rating" not in markup
    assert 'class="rc' not in markup, "a missing table leaves no empty shell"


def test_the_discipline_and_the_recent_sorties_reach_the_own_page(config_dir, bus):
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Flight discipline" in markup and "Takeoffs" in markup
    assert "Recent sessions" in markup and "Last sessions" in markup


def test_the_strip_counts_a_flight_that_started_in_the_air(config_dir, bus):
    """One flight, one sortie — a pilot who spawned airborne never took off, and still flew."""
    bus.apool = StubPool(tables=("missionstats",),
                         starts=[{"ucid": "u-me-1", "air_starts": 1121},
                                 {"ucid": "u-me-2", "air_starts": 0}])
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "1,121" in markup, "the air start did not raise the sortie count above the takeoffs"
    assert "every flight that got airborne" in markup


def test_the_strip_states_a_takeoff_count_without_the_event_stream(config_dir, bus):
    """No ``missionstats`` table: the cell says which measure it carries instead of implying one."""
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "recorded takeoffs" in markup
    assert "every flight that got airborne" not in markup


def test_the_campaigns_panel_carries_credits_and_the_badge_image(config_dir, bus):
    bus.apool = StubPool(tables=("campaigns", "campaigns_servers", "credits", "players_badges"),
                         campaigns=CAMPAIGNS)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Campaigns" in markup and "Credits and badges" in markup
    assert "Syria 82" in markup and "Kola Cold War" in markup
    assert "1,234" in markup and "87" in markup
    assert 'src="https://example.com/veteran.png"' in markup, "the badge image is not rendered"
    assert 'referrerpolicy="no-referrer"' in markup, "a third-party badge leaks the visitor"
    assert "running" in markup, "the open campaign is not marked"
    assert "Veteran" in markup


def test_the_campaigns_panel_is_absent_without_the_plugin_tables(config_dir, bus):
    bus.apool = StubPool(campaigns=CAMPAIGNS)  # rows, but no tables to read them from
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Credits and badges" not in markup


def test_the_rating_chart_speaks_the_pages_language(config_dir, bus):
    """The chart's own sentences and date labels come from the catalog, through the partial."""
    from services.webservice import i18n

    bus.apool = StubPool(tables=("trueskill", "trueskill_hist"), rating=RATING, history=HISTORY)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))

    async def run():
        async with asgi_client(app) as client:
            client.cookies.set(i18n.COOKIE_NAME, "de")
            return await client.get("/group1/me")

    markup = asyncio.run(run()).text
    assert "Seit 4. Mai 2025 um 3.7 Punkte gestiegen." in markup
    assert "Über diesen Zeitraum um 3.7 Punkte gestiegen." in markup
    assert "Mai 2025" in markup and "November 2026" in markup


# ------------------------------------------------------------------------------- the progress panel
#
# A sortie is a FLIGHT, so the panel reads `missionstats` and not `statistics`: one slot row can hold
# two takeoff/landing pairs, and a pilot who sits on the ramp is a row with no flight in it. These
# pins drive the shipped reconstruction (`plugins.missionstats.aar.reconstruct_sorties`) through the
# site's own read, so the page and the downloadable AAR report cannot drift apart.

def _event(event, when, *, mission_id=1, place="Batumi"):
    """One row of the event stream, in the shape the reconstruction reads."""
    return {"mission_id": mission_id, "event": event, "init_type": "F-16C_50", "init_side": "2",
            "place": place, "time": when, "mission_name": "Foothold", "mission_theatre": "Caucasus",
            "server_name": "Group-1"}


#: Three flights over two weeks, with one quiet week between them:
#: Tue 3 Nov — took off and landed (returned); then took off and crashed (lost);
#: Tue 17 Nov — took off and was killed (lost); then boarded and left without launching (not a sortie).
EVENTS = [
    _event("S_EVENT_BIRTH", datetime.datetime(2026, 11, 3, 20, 0)),
    _event("S_EVENT_TAKEOFF", datetime.datetime(2026, 11, 3, 20, 5)),
    _event("S_EVENT_LAND", datetime.datetime(2026, 11, 3, 21, 0)),
    _event("S_EVENT_BIRTH", datetime.datetime(2026, 11, 3, 21, 10)),
    _event("S_EVENT_TAKEOFF", datetime.datetime(2026, 11, 3, 21, 12)),
    _event("S_EVENT_CRASH", datetime.datetime(2026, 11, 3, 21, 40)),
    _event("S_EVENT_BIRTH", datetime.datetime(2026, 11, 17, 19, 0), mission_id=2),
    _event("S_EVENT_TAKEOFF", datetime.datetime(2026, 11, 17, 19, 5), mission_id=2),
    _event("S_EVENT_PILOT_DEAD", datetime.datetime(2026, 11, 17, 19, 30), mission_id=2),
    _event("S_EVENT_BIRTH", datetime.datetime(2026, 11, 17, 19, 40), mission_id=2),
    _event("S_EVENT_PLAYER_LEAVE_UNIT", datetime.datetime(2026, 11, 17, 19, 45), mission_id=2),
]

#: Two kills, one in each of the two weeks that held a flight.
KILLS = [{"time": datetime.datetime(2026, 11, 3, 20, 50)},
         {"time": datetime.datetime(2026, 11, 17, 19, 20)}]


def _progress(config_dir, bus, **kwargs):
    """The read's own answer for the linked pilot, scoped to the site — no page in the way."""
    from services.webservice import site_reads

    site = types.SimpleNamespace(servers=["Group-1"], key="group1")
    settings = {"tables": ("missionstats",), "events": EVENTS, "kills": KILLS}
    settings.update(kwargs)
    bus.apool = StubPool(**settings)
    return asyncio.run(site_reads.pilot_progress(site, bus.apool, "u-me-1", "quarter"))


def test_a_flight_is_a_sortie_and_a_ramp_session_is_not(config_dir, bus):
    """`aborted` counts the boarding that never took off, and it is not among the sorties."""
    progress = _progress(config_dir, bus)
    assert progress["sorties"] == 3, "a session on the ramp was counted as a sortie"
    assert progress["aborted"] == 1


def test_a_landing_is_returned_and_everything_else_is_a_loss(config_dir, bus):
    """The owner's definition: came back and did not die. Crash and pilot death are losses."""
    progress = _progress(config_dir, bus)
    assert progress["returned"] == 1
    assert progress["lost"] == 2
    assert progress["returned"] + progress["lost"] + progress["unfinished"] == progress["sorties"]
    assert progress["return_rate"] == 0.33
    assert progress["kd"] == 1.0, "kd is kills per LOST sortie"


def test_the_progress_read_answers_nothing_without_the_events_table(config_dir, bus):
    """No `missionstats` plugin, no panel — never a 500 and never an empty frame."""
    assert _progress(config_dir, bus, tables=()) is None


def test_a_quiet_week_is_a_gap_in_the_record_not_a_missing_row(config_dir, bus):
    """The weeks between the first and the last flight are all present, the empty one at zero."""
    progress = _progress(config_dir, bus)
    starts = [bucket["start"] for bucket in progress["buckets"]]
    assert starts == ["2026-11-02", "2026-11-09", "2026-11-16"], starts
    assert [bucket["sorties"] for bucket in progress["buckets"]] == [2, 0, 1]
    assert [bucket["returned"] for bucket in progress["buckets"]] == [1, 0, 0]


def test_the_longest_periods_bucket_by_month(config_dir, bus):
    """A year of weekly bars is unreadable on a phone, so `year` and `all` switch grain."""
    progress = _progress(config_dir, bus, )
    assert progress["grain"] == "week"
    from services.webservice import site_reads

    site = types.SimpleNamespace(servers=["Group-1"], key="group1")
    yearly = asyncio.run(site_reads.pilot_progress(site, bus.apool, "u-me-1", "year"))
    assert yearly["grain"] == "month"
    assert [bucket["start"] for bucket in yearly["buckets"]] == ["2026-11-01"]


def test_the_progress_model_names_its_figures_and_its_periods(config_dir, pool):
    """The section's model is pure: five period chips, four readouts, one ratio geometry."""
    from services.webservice.pages import site_me

    model = site_me.progress_view(_progress(config_dir, types.SimpleNamespace(apool=pool)))
    assert [readout["value"] for readout in model["readouts"]] == ["3", "33%", "2", "1.00"]
    assert model["empty"] is False
    assert len(model["chart"]["bars"]) == 3
    periods = site_me.period_switch("/group1/me", "quarter")
    assert [period["key"] for period in periods] == ["month", "quarter", "halfyear", "year", "all"]
    assert [period["href"] for period in periods][1] == "/group1/me?period=quarter"
    assert [period["active"] for period in periods] == [False, True, False, False, False]


def test_the_ratio_line_breaks_where_no_sortie_was_lost(config_dir, pool):
    """A week with nothing lost has no ratio; the line stops instead of drawing through it."""
    from services.webservice.pages import site_me

    model = site_me.progress_view(_progress(config_dir, types.SimpleNamespace(apool=pool)))
    assert len(model["kd"]["segments"]) == 2, "the line drew across a week with no rate"
    assert model["kd"]["empty"] is False
    assert site_me.progress_view(None) is None


def test_the_own_page_renders_the_progress_section(config_dir, bus):
    bus.apool = StubPool(tables=("missionstats",), events=EVENTS, kills=KILLS)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert "Your progress" in markup
    assert "Returned" in markup and "Lost" in markup
    assert "1.00" in markup, "the K/D readout is missing"
    assert "/group1/me?period=year" in markup, "the period chips do not carry their links"
    assert 'class="strip four"' in markup
    assert "A sortie is one flight: it counts from the moment the aircraft is airborne" in markup


# ------------------------------------------------------------------ the standing card

def test_the_standing_card_names_the_nearest_band_the_pilot_has_not_reached():
    """Place 12 of 412: the top 10 is two places away, so THAT is the target — not a top 3."""
    from services.webservice.pages import site_me

    card = site_me.standing_view({"size": 412, "place": 12, "value": 1.24, "sorties": 40,
                                  "floor": 10, "cuts": {}})
    assert card["place"] == "#12" and card["size"] == "412"
    assert card["band"] == "You are in the best quartile."
    assert card["goal"] == {"msgid": "Next step: place {target}.", "params": {"target": "#10"}}


def test_the_standing_card_never_names_a_band_the_cohort_cannot_have():
    """Twenty pilots have no top 10 to reach, so the card points at the quartile edge instead."""
    from services.webservice.pages import site_me

    card = site_me.standing_view({"size": 20, "place": 6, "value": 0.8, "sorties": 22,
                                  "floor": 10, "cuts": {}})
    assert card["band"] == "You are in the better half."
    assert card["goal"]["params"]["target"] == "#5", "ceil(20 * 0.25) is place 5"


def test_a_pilot_in_the_lower_half_is_pointed_at_the_better_half():
    from services.webservice.pages import site_me

    card = site_me.standing_view({"size": 400, "place": 300, "value": 0.2, "sorties": 30,
                                  "floor": 10, "cuts": {}})
    assert card["band"] is None, "place 300 of 400 holds no band"
    assert card["goal"]["params"]["target"] == "#200"


def test_an_unranked_pilot_is_told_how_many_flights_are_missing():
    from services.webservice.pages import site_me

    card = site_me.standing_view({"size": 400, "place": None, "value": None, "sorties": 4,
                                  "floor": 10, "cuts": {}})
    assert card["place"] is None and card["goal"] is None
    assert card["unranked"]["params"] == {"count": "6", "floor": "10"}


# ------------------------------------------------------------------ the AAR download

def test_the_aar_download_answers_the_bots_own_report_as_a_pdf(config_dir, bus, monkeypatch):
    """The route hands back the bytes the report built, with the filename the report chose.

    The builder is stubbed — the real one needs a live database and a bot — but the ROUTE is the
    real one: identity, UCID lookup, period mapping and the response are all exercised here.
    """
    import io

    from plugins.missionstats import reports as reports_module
    from services.webservice import site_reads
    from services.webservice.pages import site_me

    seen: dict = {}

    class FakeReport:
        def __init__(self, bot, ucid, name, flt):
            seen["ucid"], seen["name"], seen["period"] = ucid, name, flt.period

        async def render(self):
            class File:
                fp = io.BytesIO(b"%PDF-1.4 stubbed")
                filename = "AAR_U1.pdf"
            return File()

    monkeypatch.setattr(reports_module, "AAR", FakeReport)
    monkeypatch.setattr(site_me, "_bot", lambda: object())

    async def ucids(site, db, discord_id):
        return ["U1"]

    monkeypatch.setattr(site_reads, "my_ucids", ucids)
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    response, _ = fetch(app, "/group1/me/aar?period=quarter")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="AAR_U1.pdf"'
    assert response.content == b"%PDF-1.4 stubbed"
    assert seen == {"ucid": "U1", "name": "Viper One", "period": "quarter"}


def test_the_aar_download_refuses_a_period_the_page_does_not_offer(config_dir, bus, monkeypatch):
    """An unknown ?period= never reaches a query: it falls back to the page's own default."""
    from plugins.missionstats import reports as reports_module
    from services.webservice import site_reads
    from services.webservice.pages import site_me

    seen: dict = {}

    class FakeReport:
        def __init__(self, bot, ucid, name, flt):
            seen["period"] = flt.period

        async def render(self):
            return None

    monkeypatch.setattr(reports_module, "AAR", FakeReport)
    monkeypatch.setattr(site_me, "_bot", lambda: object())

    async def ucids(site, db, discord_id):
        return ["U1"]

    monkeypatch.setattr(site_reads, "my_ucids", ucids)
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    response, _ = fetch(app, "/group1/me/aar?period=since-the-dawn-of-time")
    assert response.status_code == 404, "no flight in the period is a sentence, not an empty PDF"
    assert seen["period"] == site_me.DEFAULT_PROGRESS_PERIOD


def test_the_own_page_draws_no_progress_panels_without_the_plugin(config_dir, bus):
    """The flight panels need the event stream; the STANDING card does not — it reads statistics.

    So a site without missionstats keeps its place in the K/D cohort, and loses only the sorties,
    the returned/lost split and the K/D line.
    """
    bus.apool = StubPool()
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    markup = fetch(app, "/group1/me")[0].text
    assert 'class="strip four"' not in markup, "the flight strip needs the event stream"
    assert "Your standing" in markup, "the standing reads statistics and must survive"


def test_the_period_parameter_reaches_the_statement_and_an_unknown_one_does_not(config_dir, bus):
    """`?period=` is validated against the panel's own vocabulary before it becomes SQL."""
    bus.apool = StubPool(tables=("missionstats",), events=EVENTS, kills=KILLS)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))
    fetch(app, "/group1/me?period=year")
    assert any("interval '1 year'" in statement for statement, _ in bus.apool.calls), \
        "the period never reached the read"
    bus.apool.calls.clear()
    fetch(app, "/group1/me?period=nonsense")
    assert any("interval '3 months'" in statement for statement, _ in bus.apool.calls), \
        "an unknown period escaped the vocabulary"
    assert not any("nonsense" in statement for statement, _ in bus.apool.calls)


def test_the_progress_section_speaks_the_pages_language(config_dir, bus):
    from services.webservice import i18n

    bus.apool = StubPool(tables=("missionstats",), events=EVENTS, kills=KILLS)
    app = as_viewer(build(config_dir)[0], viewer(roles={"DCS"}))

    async def run():
        async with asgi_client(app) as client:
            client.cookies.set(i18n.COOKIE_NAME, "de")
            return await client.get("/group1/me")

    markup = asyncio.run(run()).text
    assert "Dein Fortschritt" in markup
    assert "Zurückgekehrt" in markup and "Verloren" in markup


def test_a_flat_ratio_is_drawn_across_the_middle_not_on_the_floor():
    """Every bucket at the same ratio must still be visible: on the floor it reads as no data."""
    from services.webservice.pages import site_me

    flat = {"period": "quarter", "grain": "week", "sorties": 4, "returned": 2, "lost": 2, "kills": 2,
            "kd": 1.0, "return_rate": 0.5,
            "buckets": [{"start": "2026-11-02", "sorties": 2, "returned": 1, "lost": 1, "kills": 1},
                        {"start": "2026-11-09", "sorties": 2, "returned": 1, "lost": 1, "kills": 1}]}
    line = site_me.progress_view(flat)["kd"]
    assert [segment["points"] for segment in line["segments"]] == \
        ["0.0,50.0 100.0,50.0"], "a flat line was drawn on the floor"
    assert line["ticks"] == [{"top": 50.0, "text": "1.00"}], "a flat series names its value twice"


def test_a_rising_ratio_spans_the_plot_from_floor_to_ceiling():
    from services.webservice.pages import site_me

    rising = {"period": "quarter", "grain": "week", "sorties": 4, "returned": 2, "lost": 2,
              "kills": 4, "kd": 2.0, "return_rate": 0.5,
              "buckets": [{"start": "2026-11-02", "sorties": 2, "returned": 1, "lost": 1, "kills": 1},
                          {"start": "2026-11-09", "sorties": 2, "returned": 1, "lost": 1, "kills": 3}]}
    line = site_me.progress_view(rising)["kd"]
    assert [segment["points"] for segment in line["segments"]] == ["0.0,100.0 100.0,0.0"]
    assert line["ticks"] == [{"top": 0.0, "text": "3.00"}, {"top": 100.0, "text": "1.00"}]
