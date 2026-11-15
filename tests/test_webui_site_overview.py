"""The public site's shell, stylesheet and overview page.

WHAT THIS SUITE PINS
--------------------
* the page tree the shell registers: one ``public`` route per configured site, at the site's own
  address, beside the console's mount — and nothing at all when no site is configured;
* that every figure on the page came from ``site_reads``: the stub pool's sentinels are rendered, a
  number the pool never returned is not, and each read's statement runs exactly ONCE;
* that two sites render their own servers' figures and neither leaks into the other;
* the footer's containment: its text is the product's fixed attribution ``Powered by DCSServerBot``
  and nothing else; a site cannot set it (a ``branding.copyright`` in the site file loads, is
  accepted and renders nowhere), and the site adds only its own anchors beside it;
* the shell's own facts: ``lang`` and ``data-theme`` from the site's config, the nav filtered to the
  pages this build serves, the theme sheet and the logo served from the site's tree;
* the stylesheet's contract: every colour, type size, radius and length the components read is a
  custom property declared in one block, and no component rule carries a literal.

The browser measurements (1440/430, scroll width, page heights, the screenshot) and the contrast
ratios were taken through a real HTTP server and are reported on the card; they are not re-run here.
"""
from __future__ import annotations

import html.parser
import re
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import asgi_client, create_app, install_shell, link_or_copy
from core.utils.helper import ThreadSafeDict
from services.webservice import i18n, mount, permissions, site_pages, sites, theme_css
from services.webservice.pages import site_leaderboard, site_overview

REPO = Path(__file__).resolve().parent.parent
SITE_CSS = REPO / "services" / "webservice" / "static" / "site.css"

#: two sites: the root site (the untagged server) and a group's site (a ``managed_by`` token)
SERVERS_YAML = """\
Root-1:
  port: 10308
Group-1:
  managed_by:
    - 5th Guards
"""

#: the sentinels the stub pool answers, per site server. Every one of them is rendered; a number a
#: read never returned must not be on the page (see :func:`test_the_page_carries_every_read_and_no_other_figure`).
FIGURES = {
    "Root-1": {"known": 1111, "playtime": 111 * 3600, "sorties": 1113, "missions": 1115,
               "sessions": 1117, "active": 1119, "avg": 1111 * 60, "pilot": "Root Pilot",
               "hours": 111, "aircraft": "Root Jet", "aircraft_sorties": 1141,
               "peak_hour": 20, "peak_dow": 6},
    "Group-1": {"known": 2222, "playtime": 222 * 3600, "sorties": 2223, "missions": 2225,
                "sessions": 2227, "active": 2229, "avg": 2221 * 60, "pilot": "Group Pilot",
                "hours": 222, "aircraft": "Group Jet", "aircraft_sorties": 2241,
                "peak_hour": 21, "peak_dow": 7},
}


# --------------------------------------------------------------------------- doubles

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
    """A pool whose answers depend on the site's bound server names — the scope, exercised."""

    def __init__(self, squadrons=None):
        self.calls: list[tuple[str, dict]] = []
        #: the squadron rows the read answers (a test sets them to exercise that section)
        self.squadrons: list[dict] = list(squadrons or [])

    def connection(self):
        return _Connection(self)

    def _figures(self, params):
        names = list((params or {}).get("servers") or ())
        return FIGURES.get(names[0]) if names else None

    def answer(self, statement, params):
        figures = self._figures(params)
        if figures is None:
            return []
        if "AS known_pilots" in statement:
            return [{"known_pilots": figures["known"], "total_playtime": figures["playtime"],
                     "sessions": figures["sessions"], "avg_session": figures["avg"],
                     "sorties": figures["sorties"], "missions_flown": figures["missions"],
                     "active_this_week": figures["active"],
                     "active_last_week": figures["active"] - 2}]
        if "FROM mv_site_activity" in statement:
            return [{"week": "2026-W44", "playtime": figures["playtime"], "pilots": 3,
                     "sorties": figures["sorties"]}]
        if "FROM mv_serverstats" in statement:
            return [{"server_name": (params or {}).get("servers", ["?"])[0],
                     "playtime": figures["playtime"], "sorties": figures["sorties"]}]
        if "FROM mv_site_pilot_totals" in statement:
            return [{"ucid": "u-" + figures["pilot"], "name": figures["pilot"],
                     "value": figures["playtime"]}]
        if "FROM mv_site_aircraft" in statement:
            return [{"slot": figures["aircraft"], "playtime": figures["playtime"],
                     "sorties": figures["aircraft_sorties"]}]
        if "FROM mv_site_hours" in statement:
            if "GROUP BY hour" in statement:
                return [{"hour": figures["peak_hour"], "seconds": figures["hours"] * 3600,
                         "sessions": figures["sessions"]}]
            return [{"dow": figures["peak_dow"], "seconds": figures["hours"] * 3600,
                     "sessions": figures["sessions"]}]
        if "WITH site_players AS" in statement:
            return list(self.squadrons)
        if "campaign_points" in statement:
            return []
        if "to_regclass" in statement:
            return [{"present": False}]
        return []


class StubBus:
    """The in-process service bus: a pool and the live server registry the site reads."""

    def __init__(self, servers=None):
        self.apool = StubPool()
        self.servers = _registry(*(servers or default_servers()))


def _server(name, managed_by=None):
    locals_ = {} if managed_by is None else {"managed_by": [managed_by]}
    return types.SimpleNamespace(name=name, locals=locals_, status=None, players={})


def _registry(*servers) -> ThreadSafeDict:
    return ThreadSafeDict({server.name: server for server in servers})


def default_servers():
    return (_server("Root-1"), _server("Group-1", "5th Guards"))


@pytest.fixture
def bus():
    """Install a stub ``ServiceBus`` singleton for the duration of one test."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    previous = ServiceRegistry._singletons.get(ServiceBus)
    stub = StubBus()
    ServiceRegistry._singletons[ServiceBus] = stub
    try:
        yield stub
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous


@pytest.fixture(autouse=True)
def restore_console_mount():
    """``mount``'s prefix is process state; a site install must not leak into the next test."""
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


def site_config(**overrides):
    """Two sites: the root site and the group's, with the shell's own defaults."""
    root = {"slug": "", "branding": {"title": "Root Site"}}
    root.update(overrides.get("root") or {})
    group = {"slug": "group1", "group_role": "5th Guards",
             "branding": {"title": "5th Guards"}}
    group.update(overrides.get("group1") or {})
    return {"sites": {"root": root, "group1": group}}


def build(config_dir, config=None):
    """The real shell, from the production factory, with a site (or two) configured."""
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=config or site_config())
    return app, registrar


# ------------------------------------------------------------------- the routes that are served

def test_the_site_owns_both_prefixes_beside_the_console(config_dir, bus):
    app, registrar = build(config_dir)
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    assert "/" in owned, "the root site is served at the site root"
    assert "/group1" in owned, "a group's site is served at its own prefix"
    assert "/console" in owned, "the console moved off the site root"
    # the site's paths are PUBLIC: no capability and no session is asked for
    assert registrar.capability_for(path="/") == permissions.PUBLIC
    assert registrar.capability_for(path="/group1") == permissions.PUBLIC


def test_no_site_configuration_registers_no_site_route(config_dir):
    app, registrar = build(config_dir, config={"sites": {}})
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    assert "/" in owned, "without a site the console keeps the site root"
    assert site_pages.OWNER not in registrar.owners


def test_an_unknown_slug_is_not_found(config_dir, bus):
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/nope")).status_code

    assert asyncio.run(run()) == 404


# --------------------------------------------------------------------- the figures and the scope

def test_the_page_carries_every_read_and_no_other_figure(config_dir, bus):
    app, _ = build(config_dir)

    async def run():
        async with asgi_client(app) as client:
            response = await client.get("/")
        return response

    response = __import__("asyncio").run(run())
    assert response.status_code == 200
    markup = response.text
    # one sentinel per read: the hero figure and the readout strip (community_totals), the week
    # figures (activity_over_time), the standings (leaderboard) and the server row (server_board)
    for expected in ("111", "1,111", "1,113", "1,115", "1,117", "1,119", "Root Pilot", "Root-1"):
        assert expected in markup, f"'{expected}' did not reach the page"
    # a figure NO read returned, and the stub's other site's numbers, must not appear
    for absent in ("9999999", "2222", "Group Pilot", "Group-1"):
        assert absent not in markup, f"'{absent}' is on the page but no read of this site returned it"


def test_each_read_runs_exactly_once(config_dir, bus):
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get("/")

    asyncio.run(run())
    statements = [statement for statement, _params in bus.apool.calls]
    markers = {
        "community_totals": "AS known_pilots",
        "activity_over_time": "TO_CHAR(week_start",
        "server_board": "FROM mv_serverstats",
        "leaderboard": "FROM mv_site_pilot_totals",
        "squadrons": "WITH site_players AS",
    }
    for read, marker in markers.items():
        counted = sum(1 for statement in statements if marker in statement)
        assert counted == 1, f"{read} ran {counted} times; the page runs each read once"
    # the two panel reads: one statement lists the airframes, two read the hour histogram's axes
    assert sum(1 for statement in statements if "FROM mv_site_aircraft" in statement) == 1
    assert sum(1 for statement in statements if "FROM mv_site_hours" in statement) == 2
    # and every statement is bound to THIS site's server
    bound = [params["servers"] for _statement, params in bus.apool.calls
             if params and "servers" in params]
    assert bound and all(servers == ["Root-1"] for servers in bound)


SVG_ELEMENT = re.compile(r"<svg\b.*?</svg>", re.S)


def without_charts(markup: str) -> str:
    """The page with both chart SVGs removed.

    An SVG's `cx`/`cy`/`d` values are geometry, not figures: a coordinate can spell the digits of a
    sentinel a figure test looks for, so the figures are read from the document's own text.
    """
    return SVG_ELEMENT.sub("", markup)


def test_the_server_board_says_all_time_not_a_trailing_window(config_dir, bus):
    """The board reads mv_serverstats, which is all-time; its copy must not claim 30 days."""
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    markup = asyncio.run(run())
    assert "all time" in markup
    assert "last 30 days" not in markup
    assert "last thirty days" not in markup


def test_two_sites_show_their_own_servers_figures(config_dir, bus):
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text, (await client.get("/group1")).text

    root, group = asyncio.run(run())
    assert "Root Pilot" in root and "Root-1" in root
    assert "Group Pilot" not in root and "Group-1" not in root
    assert "Group Pilot" in group and "Group-1" in group
    assert "Root Pilot" not in group and "Root-1" not in group
    # the head's asset URLs carry a cache-busting version; the figures live below the header
    figures = without_charts(group).split("</header>", 1)[-1].split("<footer")[0]
    assert "222" in figures and "111" not in figures


def test_no_database_renders_the_empty_state(config_dir):
    """A shell whose bus carries no pool answers the page's own sentence, not a 500."""
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get("/")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert "The statistics database is not available yet" in response.text
    assert "Average sortie" in response.text, "the board still renders, at zero"


# ------------------------------------------------------------------------------- the footer

class _Collector(html.parser.HTMLParser):
    """Collects every element name and attribute name the markup holds."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(name for name, _value in attrs)

    def handle_data(self, data):
        self.text.append(data)


def footer_document(config_dir, copyright=None, footer_links=None):
    branding = {"title": "Root Site"}
    if copyright is not None:
        branding["copyright"] = copyright
    if footer_links is not None:
        branding["footer_links"] = footer_links
    config = site_config(root={"branding": branding})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    return asyncio.run(run())


def footer_of(markup: str) -> str:
    """The footer element's own markup, tags intact."""
    return "<footer" + markup.split("<footer", 1)[1].split("</footer>", 1)[0] + "</footer>"


def footer_text(markup: str) -> str:
    """Every character the footer prints, tags removed."""
    collector = _Collector()
    collector.feed(footer_of(markup))
    return "".join(collector.text)


def test_the_footer_is_the_fixed_attribution_and_its_links(config_dir, bus):
    """The footer's only text is ``Powered by DCSServerBot`` plus the site's own links."""
    markup = footer_document(config_dir, "Amber Wing",
                             [{"label": "Imprint", "href": "/imprint"}])
    footer = footer_of(markup)
    collector = _Collector()
    collector.feed(footer)
    text = [" ".join(part.split()) for part in collector.text if part.strip()]
    assert text == ["Powered by DCSServerBot", "Imprint"], text
    assert [tag.lower() for tag in collector.tags] == ["footer", "div", "span", "span", "a"], footer
    assert "Kept the way the bot wrote it." not in footer


def test_the_attribution_carries_no_year_and_no_holder(config_dir, bus):
    """No ``©`` and no render year: the attribution is one fixed product line, not a copyright."""
    import datetime

    markup = footer_document(config_dir)
    text = footer_text(markup)
    assert "Powered by DCSServerBot" in text
    assert "©" not in text and "copyright" not in text.lower()
    assert str(datetime.datetime.now().year) not in text
    assert footer_of(markup).count("Root Site") == 0, "the footer repeats the title the header already says"


def test_the_attribution_is_the_same_in_every_language(config_dir, bus):
    """A German site prints the English attribution: it is the product's line, never a translation."""
    from services.webservice import i18n

    markup = page_markup(build(config_dir, config=site_config(root={"lang": "de"}))[0], "/")
    assert "Die Bestenliste" in markup, "the page did not render in German"
    assert footer_text(markup).strip() == "Powered by DCSServerBot", footer_text(markup)
    translations = i18n.translations_for("de")
    assert translations.gettext("Powered by DCSServerBot") == "Powered by DCSServerBot", (
        "the attribution is in the catalog, so a catalog could change it")


def test_a_configured_copyright_renders_nowhere(config_dir, bus):
    """A site file that still sets ``branding.copyright`` loads, and the string is in no byte of the
    rendered document — the page's title, its bar and its footer carry only the site's own values."""
    markup = footer_document(config_dir, "ACME Corp",
                             [{"label": "Imprint", "href": "/imprint"}])
    assert "ACME Corp" not in markup, "the site's copyright reached the document"
    assert "Powered by DCSServerBot" in footer_text(markup)


@pytest.mark.parametrize("value", [
    "<b>ACME Corp</b>",
    "ACME Corp\nRoot Site",
    "A" * 500,
    "   ",
])
def test_any_copyright_value_the_file_carries_is_accepted_and_dropped(value):
    """The key is accepted whatever it holds — a file written for an older release keeps loading —
    and nothing of it survives into the parsed site."""
    catalog = sites.parse_sites({"root": {"branding": {"copyright": value, "title": "Root Site"}}})
    root = catalog.site_for_slug("")
    assert root is not None
    assert root.branding.title == "Root Site"
    assert not hasattr(root.branding, "copyright")
    assert root.branding.footer_links == ()


def test_the_branding_vocabulary_no_longer_names_the_copyright():
    """``copyright`` is not a branding key: it is an ACCEPTED-AND-IGNORED legacy key, listed apart."""
    assert "copyright" not in sites.BRANDING_KEYS
    assert "copyright" in sites.IGNORED_BRANDING_KEYS
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"branding": {"holder": "ACME Corp"}}})
    assert "branding.holder" in str(refusal.value), "an unknown key is still refused"


def test_footer_links_render_as_anchors_with_a_validated_target(config_dir, bus):
    markup = footer_document(config_dir, "Amber Wing",
                             [{"label": "Imprint", "href": "/imprint"},
                              {"label": "Discord", "href": "https://discord.gg/example"}])
    assert 'href="/imprint"' in markup
    assert 'href="https://discord.gg/example"' in markup
    assert '<a href="https://discord.gg/example" rel="noopener">Discord</a>' in markup
    assert '<a href="/imprint">Imprint</a>' in markup


def test_the_footer_links_stay_right_of_the_attribution_in_order(config_dir, bus):
    """The attribution is the footer's first cell and the site's anchors follow it, in file order."""
    markup = footer_document(config_dir, "Amber Wing",
                             [{"label": "Imprint", "href": "/imprint"},
                              {"label": "Discord", "href": "https://discord.gg/example"}])
    footer = footer_of(markup)
    assert footer.index("Powered by DCSServerBot") < footer.index("/imprint") < footer.index(
        "https://discord.gg/example")
    assert footer.count("Powered by DCSServerBot") == 1


def test_no_site_key_can_configure_the_attribution_away():
    """The three closed doors, pinned together: no branding key names the footer, ``theme.stylesheet``
    (a free-form sheet path) is refused by name, and a token sheet may not carry a component rule —
    so nothing in a site's block can hide or rewrite the attribution."""
    assert not [key for key in sites.BRANDING_KEYS if "footer" in key and key != "footer_links"]
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"theme": {"stylesheet": "site/theme.css"}}})
    assert "theme.stylesheet" in str(refusal.value)
    refused = theme_css.validate(".foot { display: none; }")
    assert refused is not None, "a component rule was accepted as a token sheet"


@pytest.mark.parametrize("href", ["javascript:alert(1)", "//evil.example/x", "data:text/html,x",
                                  "imprint", "http://a b"])
def test_a_footer_link_with_an_uncheckable_target_is_refused(href):
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"branding": {"footer_links": [{"label": "x", "href": href}]}}})
    assert "footer_links[0].href" in str(refusal.value)


def test_a_footer_link_entry_is_checked_exhaustively():
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"branding": {"footer_links": [
            {"label": "x", "href": "/", "class": "hot"}]}}})
    assert "footer_links[0].class" in str(refusal.value)
    with pytest.raises(sites.SiteConfigError) as refusal:
        sites.parse_sites({"root": {"branding": {"footer_links": [{"href": "/"}]}}})
    assert "footer_links[0].label" in str(refusal.value)


def test_the_schema_accepts_a_footer_links_block(tmp_path):
    """The schema the config loader validates against accepts the footer's anchors AND the retired
    ``copyright`` key — a legacy block that still carries it must keep validating."""
    from test_webui_sites import _validate

    _validate("""
DEFAULT:
  frontend: true
  sites:
    root:
      slug: ""
      branding:
        copyright: "Amber Wing"
        footer_links:
          - {label: Imprint, href: "/imprint"}
          - {label: Discord, href: "https://discord.gg/example"}
""", tmp_path)


# -------------------------------------------------------------------------------- the shell

def test_the_shell_states_the_sites_language_and_theme(config_dir, bus):
    config = site_config(root={"lang": "de", "theme": {"light": True}})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    markup = asyncio.run(run())
    assert 'lang="de"' in markup and 'data-theme="light"' in markup
    assert "Die Bestenliste" in markup, "the page renders in the site's language, not the visitor's"
    # the hour panel's own copy comes from the catalog too, not from the English msgid
    assert "Flugstunden pro Stunde · UTC" in markup
    assert "Jeder Balken ist eine UTC-Stunde" in markup
    assert "hours flown by hour · UTC" not in markup


def test_the_nav_offers_only_the_pages_this_build_serves(config_dir, bus):
    config = site_config(root={"nav": ["overview", "leaderboard", "squadrons", "players"]})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    markup = asyncio.run(run())
    nav = markup.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/"' in nav and ">Overview<" in nav
    for served in ("/rankings", "/players"):
        assert served in nav, f"the nav omits '{served}', whose page this build serves"
    assert "/squadrons" not in markup, "the nav offers a page this build does not serve"


def test_the_group_site_nav_links_stay_inside_its_prefix(config_dir, bus):
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/group1")).text

    markup = asyncio.run(run())
    nav = markup.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/group1"' in nav
    assert 'href="/"' not in nav


def test_the_theme_sheet_is_served_for_a_site_that_names_an_expert_block(config_dir, bus):
    config = site_config(root={"theme": {"expert": "--brass: #e8b04b;\n"}})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text, await client.get("/theme.css")

    markup, sheet = asyncio.run(run())
    assert 'href="/theme.css"' in markup
    assert sheet.status_code == 200
    assert sheet.text.strip() == ":root {\n  --brass: #e8b04b;\n}"
    assert sheet.headers["content-type"].startswith("text/css")


def test_a_site_without_a_palette_serves_the_default_one_but_links_none(config_dir, bus):
    """The address answers for every site; a site that names no palette carries no link to it.

    The route exists because a route cannot be registered by a save: a site that declares nothing
    today may name a preset tomorrow, and the sheet is resolved per request. Its answer is then the
    shipped palette restated — the same values ``site.css`` already carries — so the page needs no
    second request and sends none.
    """
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text, await client.get("/theme.css")

    markup, sheet = asyncio.run(run())
    assert "/theme.css" not in markup, "a site with no palette sends no second request"
    assert sheet.status_code == 200
    assert sheet.text == theme_css.preset_sheet(theme_css.DEFAULT_PRESET)


def test_only_a_raster_logo_is_served(config_dir, bus):
    (config_dir / "crest.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (config_dir / "crest.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>",
                                         encoding="utf-8")
    config = site_config(root={"branding": {"title": "Root Site", "logo": "crest.png"}})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text, await client.get("/logo.png")

    markup, image = asyncio.run(run())
    assert 'src="/logo.png"' in markup
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"

    # an SVG is never served: it can carry script, so no route exists and the shell falls back to
    # the product mark instead of drawing the declared file
    svg_config = site_config(root={"branding": {"title": "Root Site", "logo": "crest.svg"}})
    app, _ = build(config_dir, config=svg_config)

    async def run_svg():
        async with asgi_client(app) as client:
            return (await client.get("/")).text, await client.get("/logo.png")

    markup, image = asyncio.run(run_svg())
    assert 'src="/logo.png"' not in markup
    assert image.status_code == 404
    assert 'src="/static/logo-mark.png"' in markup, "an unservable logo left the top bar with no mark"


def test_the_top_bar_shows_the_product_mark_when_a_site_declares_no_logo(config_dir, bus):
    """No ``branding.logo``: the top bar carries the shipped ICON mark, never the whole lockup."""
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    markup = asyncio.run(run())
    header = markup.split("</header>", 1)[0]
    assert 'class="brand-logo" src="/static/logo-mark.png"' in header, \
        "a site with no logo shows no mark at all"
    assert 'src="/static/logo-256.png"' not in header, "the whole lockup is drawn beside the title"
    assert 'alt="DCSServerBot logo"' not in header, "the mark is decorative beside the site title"


def test_a_sites_own_logo_wins_over_the_product_mark(config_dir, bus):
    (config_dir / "crest.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    config = site_config(root={"branding": {"title": "Root Site", "logo": "crest.png"}})
    app, _ = build(config_dir, config=config)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get("/")).text

    header = asyncio.run(run()).split("</header>", 1)[0]
    assert 'src="/logo.png"' in header
    assert 'class="brand-logo"' not in header, "the product mark overrode the site's own logo"


def test_a_signed_in_visitor_differs_only_by_the_chip(config_dir, bus):
    app, _ = build(config_dir)
    import asyncio

    from services.webservice.auth import Identity

    async def run():
        async with asgi_client(app) as client:
            anonymous = (await client.get("/")).text
            app.state.webui_auth = types.SimpleNamespace(
                authenticate=lambda request: Identity(subject="specialk", backend="local",
                                                      roles=frozenset({"Admin"})))
            signed = (await client.get("/")).text
            return anonymous, signed

    anonymous, signed = asyncio.run(run())
    assert "Community record" in anonymous and "Community record" in signed
    assert "Command Center" not in anonymous.split("</header>")[0]
    assert "Command Center" in signed.split("</header>")[0]
    strip = lambda markup: markup.split("</header>", 1)[1]
    assert strip(anonymous) == strip(signed), "only the top bar's chip may differ"


# ------------------------------------------------------------------------- the page's model

def test_the_chart_draws_one_point_per_bucket_and_none_for_an_empty_window():
    weeks = [{"week": f"2026-W{number:02d}", "playtime": number * 3600, "pilots": 1}
             for number in range(36, 41)]
    geometry = site_overview.chart(weeks)
    assert len(geometry["points"]) == 5
    assert geometry["empty"] is False
    assert geometry["range"] == "W36 – W40"
    assert geometry["line"].startswith("M") and geometry["line"].count("L") == 4
    assert geometry["area"].endswith("Z")
    assert geometry["peak"]["hours"] == 40
    # the axis maximum covers the tallest bucket and every gridline is a distinct whole number
    labels = [line["label"] for line in geometry["gridlines"]]
    assert labels[0] == "0" and len(set(labels)) == len(labels)
    assert int(labels[-1].replace(",", "")) >= 40

    empty = site_overview.chart([])
    assert empty["empty"] is True and empty["points"] == [] and empty["line"] == ""


def test_the_week_figures_and_the_readouts_come_from_the_reads():
    model = site_overview.view(
        {"known_pilots": 10, "total_playtime": 7200, "avg_session": 3600, "sessions": 4,
         "sorties": 6, "missions_flown": 2, "active_this_week": 3, "active_last_week": 5,
         "active_change": -2},
        {"window": "30d", "weeks": [{"week": "2026-W44", "playtime": 7200, "pilots": 2},
                                   {"week": "2026-W45", "playtime": 3600, "pilots": 1}]},
        [{"server_name": "Root-1", "online": True, "players_online": 2, "playtime": 3600,
          "sorties": 3, "window": "all time"}],
        {"rows": [{"ucid": "u1", "name": "Pilot", "value": 7200}]},
        [{"id": 1, "name": "Sqn", "members": 4, "campaign_points": 100, "rating": None,
          "rating_available": False}],
    )
    assert model["hero"]["hours"] == "2"
    assert model["live"]["servers_up"] == "1" and model["live"]["pilots_flying"] == "2"
    assert [readout["value"] for readout in model["readouts"]][:3] == ["10", "1:00", "6"]
    # the delta carries its sign, so a negative week reads as one
    assert model["readouts"][3]["change"] == "-2"
    assert model["readouts"][3]["share"] == 30.0
    # no readout states how its number is computed
    assert all("note" not in readout and "note_count" not in readout
               for readout in model["readouts"])
    assert model["activity"]["figures"]["combined"] == 3
    assert model["activity"]["figures"]["busiest_week"] == "W44"
    assert model["standings"]["rows"][0]["hours"] == "2"
    assert model["squadrons"][0]["share"] == 100.0
    assert model["servers"][0]["playtime"] == "1"
    assert model["window_label"] == "all time"


# ------------------------------------------------------------------------ the stylesheet

def _split_tokens(text: str) -> tuple[str, str]:
    """``(every :root block, everything after them)``.

    A ``:root`` block IS the token surface, and the responsive blocks re-declare tokens; a component
    rule is what must never carry a literal.
    """
    blocks: list[str] = []
    rest = text
    while ":root {" in rest:
        start = rest.index(":root {")
        depth = 0
        for index, char in enumerate(rest[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(rest[start:index + 1])
                    rest = rest[:start] + rest[index + 1:]
                    break
        else:
            raise AssertionError("the stylesheet has a :root block that never closes")
    return "\n".join(blocks), rest


COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _components() -> str:
    """The stylesheet's component rules, without its token blocks and without its comments."""
    _tokens, components = _split_tokens(SITE_CSS.read_text(encoding="utf-8"))
    return COMMENT.sub("", components)


def test_no_component_rule_reads_a_literal_colour_size_or_radius():
    components = _components()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", components), "a literal colour in a component rule"
    assert not re.search(r"\b(?:rgba?|hsla?)\(", components), "a literal colour in a component rule"
    for prop, value in re.findall(r"(font-size|border-radius)\s*:\s*([^;}]+)", components):
        if prop == "border-radius" and value.strip() == "50%":
            continue  # a circle, not a corner
        assert value.strip().startswith("var("), f"a literal {prop}: {value.strip()}"
    # every remaining length is a token too; only the media-query conditions spell a px width
    lengths = [match for match in re.finditer(r"[0-9.]+px", components)]
    conditions = re.findall(r"@media[^{]*", components)
    allowed = sum(len(re.findall(r"[0-9.]+px", condition)) for condition in conditions)
    assert len(lengths) == allowed, (
        f"{len(lengths) - allowed} literal length(s) outside :root: "
        f"{[match.group() for match in lengths][:5]}")


def test_every_custom_property_the_stylesheet_reads_is_declared():
    text = SITE_CSS.read_text(encoding="utf-8")
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", text))
    assert used <= declared, f"undeclared custom propert(ies): {sorted(used - declared)}"
    # the colour tokens a group's own token-only override replaces are all here
    for token in ("--ground", "--stage", "--panel", "--line", "--fg", "--fg-2", "--fg-3",
                  "--brass", "--sage", "--radius",
                  "--band", "--brass-wash", "--panel-2", "--line-2", "--sp-1", "--sp-8",
                  "--gap", "--max", "--pad", "--pad-y", "--t-2xs", "--t-xs", "--t-sm", "--t-md",
                  "--t-lg", "--t-xl", "--t-2xl"):
        assert token in declared, f"the theme contract lost {token}"


def test_the_type_scale_is_the_designed_one_and_the_floor_is_thirteen():
    """No step below 13px exists — not in a token, not in a rule, not in a media query. The display
    sizes keep a ratio, not a pixel count: they are clamp()s, so the desktop cap and the phone step
    are the only pixel values in the sheet."""
    text = SITE_CSS.read_text(encoding="utf-8")
    declarations = (re.findall(r"--t-[a-z0-9-]+\s*:\s*[^;}]+", text)
                    + re.findall(r"font-size\s*:\s*[^;}]+", text))
    sizes = [float(px) for declaration in declarations
             for px in re.findall(r"([0-9.]+)px", declaration)]
    assert sizes, "the sheet declares no type size at all"
    assert min(sizes) >= 13, f"the floor is 13px, the sheet carries {min(sizes)}px"
    expected = {"--t-2xs": 13, "--t-xs": 14, "--t-sm": 15, "--t-md": 16, "--t-lg": 19,
                "--t-xl": 22, "--t-2xl": 30}
    for token, pixels in expected.items():
        assert re.search(rf"{token}:\s*{pixels}px", text), f"{token} is not {pixels}px"
    for token, expression in (
            ("--t-display", r"clamp\(48px, 6vw, 88px\)"),
            ("--t-giant", r"clamp\(60px, 7\.6vw, 108px\)"),
            ("--t-phead", r"clamp\(38px, 4\.2vw, 60px\)"),
            ("--t-display-sm", r"clamp\(34px, 9\.5vw, 42px\)"),
            ("--t-giant-sm", r"clamp\(44px, 12vw, 60px\)")):
        assert re.search(rf"{token}:\s*{expression}", text), f"{token} lost its ratio"


def test_the_phone_block_does_not_re_inflate_the_headings():
    text = SITE_CSS.read_text(encoding="utf-8")
    phone = text.split("@media (max-width: 620px) {", 1)[1]
    assert re.search(r"--t-xl:\s*22px;\s*--t-2xl:\s*30px;\s*--t-3xl:\s*40px", phone), phone[:400]


def test_the_product_mark_is_sized_34_pixels_and_reads_the_icon_only_asset():
    """The mark beside the site title is the icon-only 34px mark, stated once as a token."""
    text = SITE_CSS.read_text(encoding="utf-8")
    assert re.search(r"--mark-h-brand:\s*34px", text), "the product mark's height token is not 34px"
    rule = re.search(r"\.brand img\.brand-logo\s*\{([^}]*)\}", text)
    assert rule, "the sheet carries no rule for the product mark"
    body = rule.group(1)
    assert re.search(r"height:\s*var\(--mark-h-brand\)", body), body
    assert re.search(r"width:\s*auto", body), body
    # the shipped file is a square, transparent icon crop — not the lockup
    from PIL import Image

    mark = Image.open(REPO / "services" / "webservice" / "static" / "logo-mark.png")
    assert mark.size == (256, 256), f"the mark is {mark.size}, not 256x256"
    assert mark.mode == "RGBA" and mark.getpixel((0, 0))[3] == 0, "the mark is not transparent"


def test_the_corner_brackets_and_the_chart_tick_strips_are_gone():
    text = SITE_CSS.read_text(encoding="utf-8")
    assert re.search(r"--bracket:\s*0", text), "--bracket is not declared 0"
    for selector in (".panel::before", ".panel::after", ".strip::before", ".strip::after",
                     ".ticks.tall"):
        assert selector not in text, f"{selector} is still drawn"


# ------------------------------------------------------------------ the chart's two geometries

TWELVE_WEEKS = [{"week": f"2026-W{number:02d}", "playtime": (300 + number * 20) * 3600,
                 "pilots": number} for number in range(31, 43)]


def test_the_two_chart_geometries_are_built_from_one_set_of_buckets():
    wide = site_overview.chart(TWELVE_WEEKS, "wide")
    narrow = site_overview.chart(TWELVE_WEEKS, "narrow")

    assert (wide["width"], wide["height"]) == (760, 250), "the desktop viewBox is not 760x250"
    assert (narrow["width"], narrow["height"]) == (400, 230), "the phone viewBox is not 400x230"

    # same buckets: same point count, same hours in the same order, same peak, same axis
    assert len(wide["points"]) == len(narrow["points"]) == len(TWELVE_WEEKS)
    assert [point["hours"] for point in wide["points"]] == \
           [point["hours"] for point in narrow["points"]]
    assert wide["peak"]["hours"] == narrow["peak"]["hours"] == 300 + 42 * 20
    assert wide["peak"]["week"] == narrow["peak"]["week"] == "2026-W42"
    assert wide["range"] == narrow["range"] == "W31 – W42"
    assert [line["label"] for line in wide["gridlines"]] == \
           [line["label"] for line in narrow["gridlines"]]

    # the phone geometry carries at most three week labels, the desktop one at most six
    assert len(wide["xlabels"]) <= 6 and len(wide["xlabels"]) == 6
    assert len(narrow["xlabels"]) <= 3 and len(narrow["xlabels"]) == 3
    assert narrow["xlabels"][0]["text"] == "W31" and narrow["xlabels"][-1]["text"] == "W42"

    # the empty state is the same geometry with nothing in it
    for variant in ("wide", "narrow"):
        empty = site_overview.chart([], variant)
        assert empty["empty"] is True and empty["points"] == [] and empty["line"] == ""


def test_one_call_yields_each_geometry_and_the_page_passes_one_weeks_list():
    model = site_overview.view({}, {"window": "30d", "weeks": TWELVE_WEEKS}, [], {}, [])
    wide = model["activity"]["chart"]
    narrow = model["activity"]["chart_narrow"]
    assert (wide["width"], wide["height"]) == (760, 250)
    assert (narrow["width"], narrow["height"]) == (400, 230)
    assert [point["hours"] for point in wide["points"]] == \
           [point["hours"] for point in narrow["points"]]


def overview_markup(config_dir, path="/"):
    app, _ = build(config_dir)
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return (await client.get(path)).text

    return asyncio.run(run())


def test_the_page_renders_both_geometries_and_the_four_figures_close_the_chart_panel(config_dir, bus):
    markup = overview_markup(config_dir)
    assert 'class="chart is-wide"' in markup and 'class="chart is-narrow"' in markup
    assert markup.count('aria-label="Hours flown per week"') == 2, \
        "each geometry carries one label of its own"

    panel = markup.split("<h2>Hours per week</h2>", 1)[1].split("</section>", 1)[0]
    assert 'class="chart is-wide"' in panel and 'class="chart is-narrow"' in panel
    assert 'class="pfoot"' in panel and 'class="fig-grid four"' in panel
    # the figures close the panel, after both SVGs
    assert panel.index('class="chart is-narrow"') < panel.index('class="pfoot"')
    for label in ("This season", "Busiest", "Average week", "Quietest"):
        assert label in panel, f"'{label}' left the chart panel"
    # the c4 panel that used to hold them is gone, heading and all
    assert "<h2>The weeks</h2>" not in markup and 'class="panel c4 fill"' not in markup
    # the chart panel keeps its own meta and its own note
    assert "A week with no session is drawn at zero" in panel
    # and no sentence about how the figures were computed survives
    assert "Sum, average and both extremes" not in markup
    assert "same buckets" not in markup


def test_the_chart_panel_carries_no_tick_strip_and_the_seam_keeps_its_own(config_dir, bus):
    markup = overview_markup(config_dir)
    assert 'margin-top:10px' not in markup, "the strip under the chart is still drawn"
    panel = markup.split("<h2>Hours per week</h2>", 1)[1].split("</section>", 1)[0]
    assert 'class="ticks"' not in panel, "a tick strip is still inside the chart panel"
    seam = markup.split('<div class="boundary">', 1)[1].split("</main>", 1)[0]
    assert 'class="ticks"' in seam, "the seam lost its tick strip"


def test_the_hero_is_three_rows_of_one_viewport(config_dir, bus):
    markup = overview_markup(config_dir)
    hero = markup.split('<section class="hero">', 1)[1].split("</section>", 1)[0]
    # the three children sit directly inside .in, in this order, with no wrapper between them
    inner = hero.split('<div class="in">', 1)[1]
    assert inner.lstrip().startswith('<div class="eyebrow">'), "the eyebrow is not the first row"
    order = [inner.index('<div class="eyebrow">'), inner.index("<h1>"),
             inner.index('<div class="hero-foot">')]
    assert order == sorted(order), "the hero's rows are not eyebrow, headline, foot"
    assert inner.count("<div class=") == 2, "a fourth wrapper sits between the hero's rows"

    sheet = SITE_CSS.read_text(encoding="utf-8")
    hero_in = sheet.split(".hero .in {", 1)[1].split("}", 1)[0]
    assert "grid-template-rows: auto 1fr auto" in hero_in
    assert "var(--top-h)" in hero_in, "the hero does not fill the viewport under the top bar"
    assert "--mock-h" not in hero_in, "the hero subtracts a strip this shell does not draw"


# ------------------------------------------------- the standings' and the servers' addresses

def resolved_site(prefix=""):
    """A site context carrying only the field the address builders read."""
    return types.SimpleNamespace(prefix=prefix, slug=prefix.lstrip("/"))


def test_every_standings_row_carries_the_profile_address_of_its_own_ucid():
    pilots = {"rows": [{"ucid": "u-one", "name": "One", "value": 7200},
                       {"ucid": "u two", "name": "Two", "value": 3600},
                       {"name": "No UCID", "value": 60}]}
    site = resolved_site()
    model = site_overview.view({}, {}, [], pilots, [], resolved=site)
    hrefs = [row["href"] for row in model["standings"]["rows"]]

    # the SAME helper the leaderboard's rows link with, and the UCID of the row's own read
    assert hrefs == [site_pages.player_url(site, "u-one"),
                     site_pages.player_url(site, "u two"),
                     ""]
    assert hrefs[:2] == ["/player/u-one", "/player/u%20two"]
    assert model["standings"]["rows"][2]["href"] == "", "a row with no UCID carries no address"
    # a group's site links inside its own prefix, like every other page of that site
    grouped = site_overview.view({}, {}, [], pilots, [], resolved=resolved_site("/group1"))
    assert grouped["standings"]["rows"][0]["href"] == "/group1/player/u-one"


def test_the_rendered_standings_table_has_one_profile_anchor_per_row(config_dir, bus):
    markup = overview_markup(config_dir)
    table = markup.split('id="standings"', 1)[1].split("</table>", 1)[0]

    assert "Root Pilot" in table, "the stub's pilot is not in the standings table"
    rows = table.count("<tr") - 1  # the head row is not a pilot
    assert rows >= 1, "the pin must not pass on an empty board"
    anchors = table.count('<a class="nm" href="/player/')
    assert anchors == rows, f"{rows} standings row(s) but {anchors} profile link(s)"
    assert 'href="/player/u-Root%20Pilot"' in table, "the link is not the pilot's own UCID"


def test_a_server_rows_name_links_to_the_leaderboard_narrowed_to_that_server(config_dir, bus):
    site = resolved_site()
    model = site_overview.view(
        {}, {}, [{"server_name": "Root-1", "online": True, "playtime": 3600, "sorties": 1}],
        {}, [], resolved=site)
    href = model["servers"][0]["href"]
    split = urlsplit(href)
    # the leaderboard's OWN path and the leaderboard's OWN parameter — imported, not respelled
    assert split.path == site_pages.NAV_PATHS["leaderboard"]
    assert parse_qs(split.query) == {site_leaderboard.SERVER_PARAM: ["Root-1"]}
    # a group's site keeps its prefix here too
    grouped = site_overview.view(
        {}, {}, [{"server_name": "Root-1", "online": True, "playtime": 3600, "sorties": 1}],
        {}, [], resolved=resolved_site("/group1"))
    assert grouped["servers"][0]["href"] == "/group1/rankings?server=Root-1"


def test_the_servers_name_links_and_the_address_it_names_both_resolve(config_dir, bus):
    app, registrar = build(config_dir)
    path = site_pages.NAV_PATHS["leaderboard"]

    async def run():
        async with asgi_client(app) as client:
            return ((await client.get("/")).text, await client.get(f"{path}?server=Root-1"))

    markup, target = __import__("asyncio").run(run())
    table = markup.split('id="servers"', 1)[1].split("</table>", 1)[0]
    assert '<a class="nm" href="/rankings?server=Root-1">Root-1</a>' in table, table
    # the address the name points at is the leaderboard's own, registered and answering
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    assert path in owned
    assert target.status_code == 200


# ------------------------------------------------------------------- the server picker

def page_markup(app, path):
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            response = await client.get(path)
        assert response.status_code == 200
        return response.text

    return asyncio.run(run())


def bound_servers(bus) -> set[tuple[str, ...]]:
    """Every distinct ``servers`` tuple the page's statements were bound to."""
    return {tuple(params["servers"]) for _statement, params in bus.apool.calls
            if params and "servers" in params}


@pytest.fixture
def two_untagged_servers(bus):
    """The root site carrying TWO servers, so "all" and "one" differ in the bound tuple."""
    bus.servers = _registry(_server("Alpha"), _server("Beta"))
    return bus


def test_the_picker_scopes_every_read_of_the_page(config_dir, two_untagged_servers):
    app, _ = build(config_dir)
    page_markup(app, "/")
    assert bound_servers(two_untagged_servers) == {("Alpha", "Beta")}, \
        "an unpicked page must read every server of the site"

    two_untagged_servers.apool.calls.clear()
    page_markup(app, "/?server=Beta")
    assert bound_servers(two_untagged_servers) == {("Beta",)}, \
        "the chosen server did not scope the reads"


@pytest.mark.parametrize("query", ["", "?server=", "?server=Nope", "?server=%20"],
                         ids=["no-query", "all-servers", "unknown-name", "blank-name"])
def test_the_picker_reads_the_whole_site_for_an_empty_or_unknown_choice(
        config_dir, two_untagged_servers, query):
    """The empty choice ("All servers") and a name the site does not carry scope NOTHING: every
    read answers the site's whole server set rather than an empty page."""
    app, _ = build(config_dir)
    page_markup(app, "/" + query)
    assert bound_servers(two_untagged_servers) == {("Alpha", "Beta")}


def test_the_picker_keeps_its_choice_across_a_reload_and_clears_on_all_servers(
        config_dir, two_untagged_servers):
    app, _ = build(config_dir)
    scoped = page_markup(app, "/?server=Beta")
    picker = scoped.split('<form class="pickbar"', 1)[1].split("</form>", 1)[0]

    # the choice survives the reload: its option is the selected one, and the select states the scope
    assert '<option value="Beta" selected>Beta</option>' in picker, picker
    assert '<option value="" selected>All servers</option>' not in picker
    assert "pick-now" not in picker, "a second scope label competes with the control"
    # every server stays on offer, the chosen one included
    assert '<option value="Alpha"' in picker and '<option value="Beta"' in picker

    cleared = page_markup(app, "/?server=")
    picker = cleared.split('<form class="pickbar"', 1)[1].split("</form>", 1)[0]
    assert '<option value="" selected>All servers</option>' in picker, picker
    assert '<option value="Beta" selected>' not in picker


def test_the_picker_needs_no_javascript_and_submits_to_this_page(config_dir, two_untagged_servers):
    """A plain GET form: the select's own value is the query parameter. The visible row carries no
    submit button; the submit control is the `noscript` one, and `site-picker.js` submits on change
    only where scripting is on."""
    app, _ = build(config_dir)
    markup = page_markup(app, "/")
    form = markup.split('<form class="pickbar"', 1)[1].split("</form>", 1)[0]
    assert form.lstrip().startswith('method="get"'), form[:120]
    assert 'action="/"' in form
    assert '<select class="pick" id="site-server" name="server" data-autosubmit>' in form, form
    assert "onchange" not in form, "the picker carries an inline change handler"
    noscript = form.split("<noscript>", 1)
    assert len(noscript) == 2, form
    assert 'type="submit"' in noscript[1], "the no-JS fallback has no submit button"
    before = form.split("<noscript>", 1)[0]
    assert "pick-go" not in before, "a submit button still competes with the control in the row"

    script = REPO / "services" / "webservice" / "static" / "site-picker.js"
    handler = script.read_text(encoding="utf-8")
    assert 'select[data-autosubmit]' in handler
    assert "addEventListener(\"change\"" in handler
    assert "site-picker.js" in markup, "the page never loads the picker script"


def test_the_picker_is_offered_per_site_including_the_group_prefix(config_dir, two_untagged_servers):
    app, _ = build(config_dir)
    group = page_markup(app, "/group1")
    form = group.split('<form class="pickbar"', 1)[1].split("</form>", 1)[0]
    assert 'action="/group1"' in form, form
    assert '<option value="" selected>All servers</option>' in form


def test_the_picker_model_offers_one_option_per_server_and_nothing_else():
    options = site_overview._picker(types.SimpleNamespace(prefix=""), "", ("Alpha", "Beta"))["options"]
    assert options == [{"value": "", "label": "All servers", "selected": True},
                       {"value": "Alpha", "label": "Alpha", "selected": False},
                       {"value": "Beta", "label": "Beta", "selected": False}]


def test_the_picker_stacks_under_the_facts_at_phone_width():
    """At 430px the band is one column: the picker is a full-width row UNDER the stacked facts, its
    left rule replaced by a top hairline, and the control is bounded by its column."""
    text = SITE_CSS.read_text(encoding="utf-8")
    phone = text.split("@media (max-width: 620px) {", 1)[1]
    assert "border-top: var(--hair-w) solid var(--line-2)" in phone, \
        "the picker's phone rule lost its top hairline"
    assert "border-left: 0" in phone, "the picker keeps its left rule on a phone"
    select = text.split(".pickbar .pick {", 1)[1].split("}", 1)[0]
    assert "max-width: 100%" in select, select
    assert "font-size: var(--t-lg)" in select, "the picker does not read the facts' own size"


def test_a_narrowed_context_never_mutates_the_requests_own_membership():
    site = sites.SiteContext(name="root", slug="", prefix="", servers=frozenset({"Alpha", "Beta"}),
                             branding=sites.Branding(), theme=sites.Theme(), nav=(), tiles=(),
                             lang="en", is_root=True)
    narrowed, scope = site_overview.scope_context(site, "Beta")
    assert scope == "Beta" and narrowed.servers == frozenset({"Beta"})
    assert narrowed == sites.SiteContext(name="root", slug="", prefix="",
                                         servers=frozenset({"Beta"}), branding=site.branding,
                                         theme=site.theme, nav=(), tiles=(), lang="en",
                                         is_root=True)
    assert site.servers == frozenset({"Alpha", "Beta"}), "the request's own context was mutated"


# ------------------------------------------------------------------------ the medals

FIVE_PILOTS = {"rows": [{"ucid": f"u{position}", "name": f"Pilot {position}",
                         "value": (100 - position) * 3600} for position in range(1, 6)]}


def test_the_top_three_rows_carry_a_medal_and_the_fourth_does_not(
        config_dir, bus, monkeypatch):
    """The podium is a CLASS, never an emoji: ranks 1–3 answer m1/m2/m3, rank 4 the plain ``04``."""
    async def fake_read(resolved, pool, sections=None):
        return {"totals": {}, "activity": {}, "board": [], "pilots": dict(FIVE_PILOTS),
                "squadrons": []}

    monkeypatch.setattr(site_overview, "read", fake_read)
    markup = page_markup(build(config_dir)[0], "/")
    table = markup.split('id="standings"', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr[^>]*>.*?</tr>", table, re.S)[1:]  # the head row is not a pilot
    assert len(rows) == 5, rows

    for position, row in enumerate(rows[:3], start=1):
        assert f'class="medal m{position}"' in row, row
        assert f'<span class="medal m{position}">{position}</span>' in row, row
        assert 'class="podium"' in row, row
    assert "medal" not in rows[3] and "podium" not in rows[3], rows[3]
    assert '<td class="rk">04</td>' in rows[3], rows[3]
    assert '<td class="rk">01</td>' not in table, "the medal rows also print the two-digit rank"


def test_the_medal_classes_are_declared_in_the_shared_sheet():
    """Both pages that rank pilots read ``.medal``/``.m1``–``.m3`` from site.css, and the disc is
    drawn with CSS alone — an emoji glyph would render as a box on Windows."""
    text = SITE_CSS.read_text(encoding="utf-8")
    for rule in (r"\.medal\s*\{", r"\.medal\.m1\s*\{", r"\.medal\.m2\s*\{", r"\.medal\.m3\s*\{"):
        assert re.search(rule, text), f"the sheet carries no {rule} rule"
    assert re.search(r"border-radius:\s*50%", text.split(".medal {", 1)[1].split("}", 1)[0])
    assert not re.search(r"--medal-gold:[^;]*(?:emoji|\\\\u)", text), "the medal is a glyph, not a disc"
    # a literal colour is a token only: the medal's own colours are declared in :root
    tokens, components = _split_tokens(text)
    assert "--medal-gold" in tokens and "--medal-silver" in tokens and "--medal-bronze" in tokens


# --------------------------------------------------- every visible string is a trans string

JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
TRANS_BLOCK = re.compile(r"\{%-?\s*trans\b.*?\{%-?\s*endtrans\s*-?%\}", re.S)
JINJA_TAG = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.S)
TEXT_RUN = re.compile(r">([^<>]+)<", re.S)
LETTERS = re.compile(r"[A-Za-z]{2,}")


def unwrapped_text(template: str) -> list[str]:
    """Every run of text a visitor reads that is NOT inside a ``{% trans %}`` block.

    Jinja comments, ``{% … %}`` tags and ``{{ … }}`` expressions are removed first, so what remains
    is literal copy — the thing that must be wrapped for a later translation pass to reach it.
    """
    text = TRANS_BLOCK.sub("> <", template)
    text = JINJA_COMMENT.sub("", text)
    found = []
    for run in TEXT_RUN.findall(text):
        literal = JINJA_TAG.sub("", run)
        if LETTERS.search(literal):
            found.append(" ".join(literal.split()))
    return found


#: the ONE literal a site template prints without a ``{% trans %}``: the product's attribution. It
#: is the same words in every language, so it is never looked up in a catalog — and it is literal
#: exactly so that no catalog, and no site, can change it.
ATTRIBUTION = "Powered by DCSServerBot"


def test_no_site_template_prints_an_unwrapped_string():
    """Every visible run is a ``{% trans %}`` body, except the attribution in ``site_base.html``."""
    templates = sorted((REPO / "services" / "webservice" / "templates").glob("site_*.html"))
    assert len(templates) >= 6, f"the site's template set shrank: {[p.name for p in templates]}"
    offenders: dict[str, list[str]] = {}
    for path in templates:
        found = unwrapped_text(path.read_text(encoding="utf-8"))
        if path.name == "site_base.html" and found == [ATTRIBUTION]:
            continue
        if found:
            offenders[path.name] = found
    assert offenders == {}


def test_the_attribution_is_literal_and_carries_no_trans_block():
    """The footer's line is literal text in the shell — not a trans body, not a site value."""
    text = (REPO / "services" / "webservice" / "templates" / "site_base.html").read_text(
        encoding="utf-8")
    footer = text.split('<footer class="foot">', 1)[1]
    assert f'<span class="foot-text">{ATTRIBUTION}</span>' in footer
    assert "copyright" not in footer and "branding" not in footer.split("</span>", 1)[0]
    assert footer.count(ATTRIBUTION) == 1, "the footer prints the attribution more than once"


def test_the_unwrapped_string_scanner_finds_one_when_it_is_there():
    """The positive control: without it a scanner that never matches would pass the check above."""
    assert unwrapped_text("<p>Sign in to continue</p>") == ["Sign in to continue"]
    assert unwrapped_text("<p>{% trans %}Sign in{% endtrans %}</p>") == []
    assert unwrapped_text("<p>{{ pilot.name }}</p>") == []


# ------------------------------------------------------------------- the squadrons section

SQUADRONS = [{"id": 1, "name": "5th Guards", "description": "", "image_url": "", "members": 4,
              "campaign_points": 120, "rating": None, "rating_available": False}]

SECTION_NUMBER = re.compile(r'class="no">(\d\d)<')


def section_numbers(markup: str) -> list[str]:
    """The section numbers the page prints, in the order it prints them."""
    return SECTION_NUMBER.findall(markup)


def test_the_sections_are_numbered_without_a_gap_when_the_squadrons_section_applies():
    model = site_overview.view({}, {}, [], {}, [dict(row) for row in SQUADRONS], credits=True)
    assert model["squadrons_section"] is True
    assert model["sections"]["standings"] == "03"
    assert model["sections"]["aircraft"] == "04"
    assert model["sections"]["squadrons"] == "05"
    assert model["sections"]["servers"] == "06"
    # the aircraft section is not conditional: it numbers the same with or without the plugin
    plain = site_overview.view({}, {}, [], {}, [], credits=False)
    assert plain["sections"]["aircraft"] == "04" and plain["sections"]["servers"] == "05"


def test_a_rendered_page_numbers_its_sections_01_to_06_with_the_squadrons_section(
        config_dir, bus, monkeypatch):
    monkeypatch.setattr(site_overview, "credits_present", lambda: True)
    bus.apool.squadrons = [dict(row) for row in SQUADRONS]

    markup = overview_markup(config_dir)
    assert "<h2>The squadrons</h2>" in markup
    assert "The squadrons that fly here." in markup, "the section's intro is not printed"
    assert "5th Guards" in markup
    assert "Bar length is the squadron’s points against the leader’s" not in markup, \
        "the panel still explains how its bars are scaled"
    assert section_numbers(markup) == ["01", "02", "03", "04", "05", "06"]


@pytest.mark.parametrize("credits, squadrons", [(False, SQUADRONS), (True, [])],
                         ids=["no-creditsystem-plugin", "no-squadron-row"])
def test_the_rendered_page_carries_nothing_of_the_squadrons_section_when_it_does_not_apply(
        config_dir, bus, monkeypatch, credits, squadrons):
    monkeypatch.setattr(site_overview, "credits_present", lambda: credits)
    bus.apool.squadrons = [dict(row) for row in squadrons]

    markup = overview_markup(config_dir)
    # no heading, no intro, no note — and no points label left behind
    assert "<h2>The squadrons</h2>" not in markup
    assert "The squadrons that fly here" not in markup
    assert "No squadron has a member who flew" not in markup
    assert "Campaign points" not in markup
    assert "5th Guards" not in markup
    # the sections that do render keep reading 01–05 — no hole where the squadrons section was
    assert section_numbers(markup) == ["01", "02", "03", "04", "05"]
    assert "<h2>The servers</h2>" in markup


def test_the_squadrons_section_needs_both_the_plugin_and_a_row():
    rows = [dict(row) for row in SQUADRONS]
    assert site_overview.view({}, {}, [], {}, rows, credits=True)["squadrons_section"] is True
    assert site_overview.view({}, {}, [], {}, rows, credits=False)["squadrons_section"] is False
    assert site_overview.view({}, {}, [], {}, [], credits=True)["squadrons_section"] is False
    # the section's own rows are still composed either way; only the section is withheld
    assert site_overview.view({}, {}, [], {}, rows, credits=False)["squadrons"][0]["name"] == \
           "5th Guards"


def test_credits_presence_reads_the_bots_own_plugin_list(monkeypatch):
    """The page asks the node's configured plugins, and answers ``False`` with no bot service."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    saved = ServiceRegistry._singletons.get(BotService)
    try:
        ServiceRegistry._singletons.pop(BotService, None)
        assert site_overview.credits_present() is False, "a process without a bot has no plugin"

        ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
            bot=types.SimpleNamespace(plugins=["userstats", "creditsystem"]), node=None)
        assert site_overview.credits_present() is True
        ServiceRegistry._singletons[BotService] = types.SimpleNamespace(
            bot=types.SimpleNamespace(plugins=["userstats"]), node=None)
        assert site_overview.credits_present() is False
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


# ------------------------------------------------------------------------ the three panels

SORTIE_WEEKS = [{"week": "2026-W35", "playtime": 3600, "pilots": 1, "sorties": 100},
                {"week": "2026-W36", "playtime": 7200, "pilots": 2, "sorties": 150},
                {"week": "2026-W37", "playtime": 3600, "pilots": 1, "sorties": 150}]


def test_the_sorties_panel_draws_one_bar_per_week_of_the_hours_window():
    model = site_overview.view({}, {"window": "30d", "weeks": SORTIE_WEEKS}, [], {}, [])
    chart = model["sorties"]["chart"]
    bars = chart["bars"]

    assert len(bars) == len(SORTIE_WEEKS) == len(model["activity"]["chart"]["points"])
    assert [bar["label"] for bar in bars] == \
           [site_overview._week_label(point["week"]) for point in model["activity"]["chart"]["points"]]
    # every bar is drawn against the tallest one, and the current (last) week is marked
    assert max(bar["h"] for bar in bars) == chart["bottom"] - chart["top"]
    assert [bar["now"] for bar in bars] == [False, False, True]
    assert bars[0]["h"] < bars[1]["h"]
    assert model["sorties"]["empty"] is False


def test_the_sorties_figures_state_total_busiest_average_and_the_change_against_the_first_week():
    figures = site_overview.view({}, {"weeks": SORTIE_WEEKS}, [], {}, [])["sorties"]["figures"]
    assert figures["total"] == 400
    assert figures["busiest"] == 150 and figures["busiest_week"] == "W36"
    assert figures["average"] == round(400 / 3)
    assert figures["change"] == "+50", "the last week against the first, in percent, signed"


def test_a_first_week_without_sorties_has_no_percentage_to_state():
    weeks = [{"week": "2026-W35", "playtime": 0, "pilots": 0, "sorties": 0},
             {"week": "2026-W36", "playtime": 0, "pilots": 0, "sorties": 4}]
    figures = site_overview.view({}, {"weeks": weeks}, [], {}, [])["sorties"]["figures"]
    assert figures["change"] is None, "a zero first week is no denominator"

    model = site_overview.view({}, {"weeks": []}, [], {}, [])
    assert model["sorties"]["empty"] is True
    assert model["sorties"]["figures"]["total"] == 0
    assert model["sorties"]["chart"]["bars"] == []


def test_the_bar_geometry_has_two_variants_built_from_one_set_of_values():
    entries = [{"label": f"W{number}", "value": number * 10} for number in range(31, 43)]
    wide = site_overview.bars_chart(entries, "wide")
    narrow = site_overview.bars_chart(entries, "narrow")

    assert (wide["width"], wide["height"]) == (760, 230)
    assert (narrow["width"], narrow["height"]) == (400, 200)
    # the same proportions in both viewBoxes: each bar's share of its own vertical span
    tallest = max(bar["value"] for bar in wide["bars"])
    for chart in (wide, narrow):
        span = chart["bottom"] - chart["top"]
        for bar in chart["bars"]:
            assert bar["h"] / span == pytest.approx(bar["value"] / tallest, abs=0.01)
    assert [bar["value"] for bar in wide["bars"]] == [bar["value"] for bar in narrow["bars"]]
    assert len(wide["xlabels"]) <= 6 and len(narrow["xlabels"]) <= 3
    assert narrow["xlabels"][0]["text"] == "W31" and narrow["xlabels"][-1]["text"] == "W42"
    # no bar carries its value, in either geometry: the bar chart is read as a shape
    for chart in (wide, narrow):
        assert all("show_value" not in bar for bar in chart["bars"])
    assert all("value" in bar for bar in wide["bars"]), "the geometry still knows each bar's value"
    assert site_overview.bars_chart([])["empty"] is True


def test_the_aircraft_bars_are_scaled_against_the_leader_and_cap_at_five():
    model = site_overview.view(
        {}, {}, [], {}, [],
        aircraft={"rows": [{"slot": "F/A-18C Hornet", "playtime": 7200, "sorties": 9},
                           {"slot": "F-16C Viper", "playtime": 3600, "sorties": 4}]})
    rows = model["aircraft"]["rows"]
    assert [row["slot"] for row in rows] == ["F/A-18C Hornet", "F-16C Viper"]
    assert rows[0]["share"] == 100.0 and rows[1]["share"] == 50.0
    assert rows[0]["hours"] == "2" and rows[0]["sorties"] == "9"
    assert model["aircraft"]["empty"] is False
    assert site_overview.view({}, {}, [], {}, [], aircraft={"rows": []})["aircraft"]["empty"] is True


def test_the_hour_panel_draws_one_bar_per_utc_hour_in_flight_minutes():
    hours = [{"hour": hour, "seconds": 0, "sessions": 0} for hour in range(24)]
    hours[18]["seconds"] = 20 * 60
    hours[20]["seconds"] = 30 * 60
    hours[21]["seconds"] = 10 * 60
    weekdays = [{"dow": dow, "seconds": 0, "sessions": 0} for dow in range(1, 8)]
    weekdays[5]["seconds"] = 36 * 60
    weekdays[6]["seconds"] = 12 * 60

    model = site_overview.view({}, {}, [], {}, [], hours_of_day={"hours": hours,
                                                                "weekdays": weekdays})
    bars = model["hours"]["chart"]["bars"]

    assert len(bars) == 24, "one bar per UTC hour, no merging"
    assert [bar["label"] for bar in bars] == [f"{hour:02d}" for hour in range(24)]
    assert [bar["value"] for bar in bars] == [0] * 18 + [20, 0, 30, 10, 0, 0]
    assert sum(bar["value"] for bar in bars) == 60, "every minute is drawn exactly once"
    assert model["hours"]["figures"]["peak_hour"] == "20:00"
    assert model["hours"]["figures"]["peak_seconds"] == 30 * 60
    assert model["hours"]["figures"]["busiest_day"] == i18n._("Sat")
    assert model["hours"]["empty"] is False


def test_the_peak_hour_figure_names_the_bar_the_chart_draws_tallest():
    """The defect this panel had: the figure was computed per hour, the bars per bucket, so the two
    named different hours. One bar per hour makes agreement a property, not a coincidence."""
    hours = [{"hour": hour, "seconds": 0, "sessions": 0} for hour in range(24)]
    # the busiest hour sits in the middle of what USED to be the 15-17 bucket, and the hours around
    # it are busier in total than it is alone — the old bucketing drew that neighbour block tallest
    for hour, seconds in ((15, 90 * 60), (16, 90 * 60), (18, 50 * 60), (19, 186 * 60), (20, 60 * 60)):
        hours[hour]["seconds"] = seconds
    weekdays = [{"dow": dow, "seconds": 0, "sessions": 0} for dow in range(1, 8)]
    weekdays[0]["seconds"] = 540 * 60

    model = site_overview.view({}, {}, [], {}, [], hours_of_day={"hours": hours,
                                                                "weekdays": weekdays})
    bars = model["hours"]["chart"]["bars"]
    tallest = max(range(len(bars)), key=lambda index: bars[index]["value"])

    assert bars[tallest]["label"] == "19"
    assert f"{tallest:02d}:00" == model["hours"]["figures"]["peak_hour"] == "19:00"


def test_the_hour_panel_labels_exactly_the_hours_each_viewbox_carries():
    hours = [{"hour": hour, "seconds": hour * 60, "sessions": 1} for hour in range(24)]
    weekdays = [{"dow": dow, "seconds": 60, "sessions": 1} for dow in range(1, 8)]
    model = site_overview.view({}, {}, [], {}, [], hours_of_day={"hours": hours,
                                                                "weekdays": weekdays})

    for variant, expected in (("chart", ["00", "06", "12", "18", "23"]),
                              ("chart_narrow", ["00", "12", "23"])):
        chart = model["hours"][variant]
        assert len(chart["bars"]) == 24
        assert [label["text"] for label in chart["xlabels"]] == expected
        widths = {bar["w"] for bar in chart["bars"]}
        assert len(widths) == 1, widths
        # 24 equal slots: the same span for every bar, never a wide block beside thin ones
        slot_width = (chart["right"] - chart["left"]) / 24
        slots = [chart["bars"][index + 1]["x"] - chart["bars"][index]["x"] for index in range(23)]
        assert max(slots) - min(slots) <= 0.2, slots
        assert slot_width == pytest.approx(widths.pop() / site_overview._BAR_SHARE, abs=0.2)
    assert site_overview.HOUR_LABELS["wide"] == (0, 6, 12, 18, 23)
    assert site_overview.HOUR_LABELS["narrow"] == (0, 12, 23)


def test_a_site_without_a_sortie_draws_the_full_axis_at_zero_and_no_peak():
    hours = [{"hour": hour, "seconds": 0, "sessions": 0} for hour in range(24)]
    weekdays = [{"dow": dow, "seconds": 0, "sessions": 0} for dow in range(1, 8)]
    model = site_overview.view({}, {}, [], {}, [], hours_of_day={"hours": hours,
                                                                "weekdays": weekdays})
    assert len(model["hours"]["chart"]["bars"]) == 24
    assert model["hours"]["empty"] is True
    assert model["hours"]["figures"]["peak_hour"] == ""
    assert model["hours"]["figures"]["busiest_day"] == ""

    # and a caller that names neither read draws both panels' empty states instead of a 500
    model = site_overview.view({}, {}, [], {}, [])
    assert model["hours"]["empty"] is True and model["aircraft"]["empty"] is True
    assert model["hours"]["chart"]["bars"][0]["value"] == 0
    assert len(model["hours"]["chart"]["bars"]) == 24
    assert model["hours"]["figures"]["peak_hour"] == ""


def test_the_three_panels_render_on_the_page_with_their_figures(config_dir, bus):
    markup = overview_markup(config_dir)
    assert "<h2>Sorties per week</h2>" in markup
    assert "<h2>Most flown</h2>" in markup
    assert "<h2>When we fly</h2>" in markup
    assert "<h2>The aircraft</h2>" in markup
    assert "Root Jet" in markup, "the aircraft read's slot did not reach the page"
    assert "1,141 sorties" in markup
    assert "20:00" in markup and "Peak hour" in markup
    assert "Sat" in markup and "Sun" not in markup
    # the hour panel says what it measures: flight time per UTC hour, one bar per hour
    assert "hours flown by hour · UTC" in markup
    assert ("Every bar is one UTC hour: flight time is split across the hours each session "
            "actually spanned.") in markup
    assert "sorties by hour · UTC" not in markup
    assert "Every bar covers the hours it is labelled with" not in markup
    # both bar charts carry one label per geometry, and no chart library is loaded
    assert markup.count('aria-label="Sorties flown per week"') == 2
    assert markup.count('aria-label="hours flown by hour · UTC"') == 2
    assert "<script" not in markup.split("<main", 1)[1].split("</main>", 1)[0]


def test_the_three_panels_render_their_empty_state_on_a_site_without_data(config_dir, bus):
    bus.apool.answer = lambda statement, params: []
    markup = overview_markup(config_dir)
    assert "This window holds no recorded sortie yet." in markup
    assert "No aircraft has been flown on these servers yet." in markup
    assert "No sortie has been recorded on these servers yet." in markup
    # the four and two figures still close their panels, at zero
    assert "vs. the first week" in markup



# ------------------------------------------------------- the rendered page in a real browser
#
# The checks below need layout, not markup: a size the cascade resolved, a popover's own placement,
# a native list's background, a column grid. They render the REAL templates through the app and
# measure them in headless Chromium with the shipped `site.css`. The measuring runs in its own
# process (`tests/site_visual_probe.py`) because Playwright's sync API leaves an event loop
# installed in the process that hosts it, which would break the `asyncio.run` calls above.

SITE_PROBE = REPO / "tests" / "site_visual_probe.py"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """``{(page, width): measurements}`` plus the screenshot directory, from the real templates."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    directory = tmp_path_factory.mktemp("site-rendered")
    config = directory / "config"
    config.mkdir()
    (config / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")

    previous = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = StubBus()
    try:
        app, _ = build(config)
        document = {"overview": page_markup(app, "/"), "leaderboard": page_markup(app, "/rankings")}
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous

    link_or_copy(REPO / "services" / "webservice" / "static", directory / "static")
    for name, markup in document.items():
        (directory / f"{name}.html").write_text(markup, encoding="utf-8")

    import json
    import subprocess

    shots = directory / "shots"
    finished = subprocess.run([sys.executable, str(SITE_PROBE), str(directory), str(shots)],
                              capture_output=True, text=True, encoding="utf-8", timeout=180, check=True)
    payload = json.loads(finished.stdout.strip().splitlines()[-1])
    if isinstance(payload, dict) and payload.get("unavailable"):
        pytest.skip(payload["unavailable"])
    return {(row["page"], row["width"]): row for row in payload}, shots


def test_the_hero_is_eighty_six_pixels_at_1440_and_forty_one_on_a_phone(rendered):
    """The display size is a clamp(), so only a resolved cascade can state it: 6vw of 1440 is 86.4
    and the phone step lands on 40.85 — the note's 86 and 41. The 13px floor holds at both."""
    measurements, _shots = rendered
    assert measurements[("overview", 1440)]["hero"] == pytest.approx(86, abs=0.6)
    assert measurements[("overview", 430)]["hero"] == pytest.approx(41, abs=0.6)
    desktop = measurements[("overview", 1440)]
    assert desktop["body"] == 16
    assert desktop["sectionHeading"] == 30
    assert desktop["panelHeading"] == 22
    for key in (("overview", 1440), ("overview", 430), ("leaderboard", 1440), ("leaderboard", 430)):
        row = measurements[key]
        assert row["smallestText"] >= 13, f"{row['smallestText']}px painted at {row['width']}"
        assert row["underThirteen"] == 0, f"{row['underThirteen']} run(s) under 13px at {row['width']}"
        assert row["scrollWidth"] == row["clientWidth"], f"horizontal scroll at {key}"


def test_the_language_menu_opens_below_its_pill_and_not_in_the_middle(rendered):
    """A popover with no placement rule is centred by the browser; the sheet's `:popover-open` rule
    must win at both widths."""
    measurements, shots = rendered
    for width, height in ((1440, 900), (430, 844)):
        row = measurements[("overview", width)]
        language = row["language"]
        assert language["open"], f"the menu does not open at {width}"
        assert language["position"] in ("absolute", "fixed"), language["position"]
        assert language["top"] >= language["pillBottom"] - 1, \
            f"the menu is not under its pill: {language['top']} < {language['pillBottom']}"
        assert language["top"] < height / 3, \
            f"the menu fell back to the centred placement: top {language['top']}"
        assert abs(language["right"] - language["pillRight"]) <= 2, language
        assert (shots / f"overview-langopen-fold-{width}x{height}.png").exists()


def test_the_picker_reads_the_facts_own_type_and_its_list_has_a_ground(rendered):
    """Same face, same size, same baseline as the facts — and an `<option>` with a real ground, so
    the native list is not the page's ink on the OS's white."""
    measurements, _shots = rendered
    desktop = measurements[("overview", 1440)]
    assert desktop["pickSize"] == desktop["factsSize"], desktop
    assert desktop["pickFamily"] == desktop["factsFamily"], desktop
    assert desktop["optionBackground"] not in ("", "transparent", "rgba(0, 0, 0, 0)"), desktop
    assert desktop["optionColour"] not in ("", "transparent", "rgba(0, 0, 0, 0)"), desktop
    phone = measurements[("overview", 430)]
    assert phone["pickSize"] == phone["factsSize"], phone


def test_the_rendered_charts_carry_no_bar_value_nodes(rendered):
    """The macro no longer emits a value above a bar; the DOM is the proof, not the template."""
    measurements, _shots = rendered
    desktop = measurements[("overview", 1440)]
    assert desktop["bars"] > 0, "the chart drew no bar at all, so the pin proves nothing"
    assert desktop["barValues"] == 0, desktop


def test_the_control_rows_share_one_label_column_at_both_widths(rendered):
    """Every label occupies the same column and every chip group starts on the same x — the thing
    the three rows failed to do — and the current choice is marked at both widths."""
    measurements, _shots = rendered
    desktop = measurements[("leaderboard", 1440)]
    assert len(desktop["labelStarts"]) >= 4, desktop  # three control rows and the pager
    assert len(set(desktop["labelStarts"])) == 1, desktop
    assert len(set(desktop["chipStarts"])) == 1, desktop
    assert desktop["activeControls"] >= 3, "the current choice is not marked"
    phone = measurements[("leaderboard", 430)]
    assert len(set(phone["labelStarts"])) == 1, phone
    assert len(set(phone["chipStarts"])) == 1, phone


def test_the_picker_submits_the_chosen_server_with_and_without_scripting(rendered):
    """Both paths reach the same address: the change handler when scripting is on, the noscript
    button when it is off. Same query parameter, same page."""
    measurements, _shots = rendered
    desktop = measurements[("overview", 1440)]
    on_change = desktop["submittedOnChange"]
    without = desktop["submittedWithoutScript"]
    assert on_change.endswith("?server=Root-1"), on_change
    assert without.endswith("?server=Root-1"), without


# ------------------------------------------------ the servers panel's closing copy

#: A public page states what it shows. These fragments are a list of what the site withholds, which
#: reads as an inventory of what it holds, and none of them may be printed anywhere on the site.
WITHHELD_FRAGMENTS = (
    "No Discord IDs",
    "no IP address",
    "no ban or watch state",
    "Discord linked",
    "Not linked to a Discord account",
)


def test_the_servers_panel_closes_on_its_table_and_prints_no_withheld_inventory(config_dir, bus):
    """The panel ends with the table above it: no note naming what the site does not publish.

    The table is the positive half of the pin — an empty page could not satisfy the absence."""
    markup = overview_markup(config_dir)
    table = markup.split('id="servers"', 1)[1].split("</table>", 1)[0]
    assert "Root-1" in table, "the servers table did not render"
    assert table.count("<tr") > 1, "the servers table carries no row"
    for fragment in WITHHELD_FRAGMENTS:
        assert fragment not in markup, f"{fragment!r} is printed on the site overview"
