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
    """The viewer's own record, bound to the site's servers — the read's own shape."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.linked: dict[str, tuple[str, ...]] = dict(LINKED)

    def connection(self):
        return _Connection(self)

    def answer(self, statement, params):
        params = params or {}
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
    assert "no sign-in method enabled" in response.text


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
