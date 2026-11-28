"""The public site's squadrons: the list in both layouts, and one squadron's page.

WHAT THIS SUITE PINS
--------------------
* the list route answers a served site, and draws the layout the site STORES — cards or the dense
  table — with the other branch absent, so a template that always draws one of them cannot pass;
* a site that stores no ``layout:`` key renders the shipped default (cards), never a blank page;
* the list's two control rows belong to the table: ``?by=`` re-orders it (an unknown value falls
  back to campaign points) and ``?server=`` narrows it to one of the site's servers (a name that is
  not this site's falls back to the whole site), each link carrying the other parameter; the cards
  layout renders neither row and ignores both parameters;
* a site block that names an unknown page id, or a layout the page does not offer, is refused at
  STARTUP with the offending key in the message;
* one squadron's page answers 200 for a squadron of this site and the site's not-found page for an
  id that is not one of them — including a squadron of another site, which no site serves;
* the roster lists only members with a row on THIS site's servers: a member who flew only on
  another site's server is absent from it;
* an empty site renders one sentence, not an empty table and not an empty grid;
* a squadron's mark is printed only when ``image_url`` is an absolute ``http(s)`` URL: a
  ``javascript:``, ``data:``, protocol-relative or non-URL value draws the monogram instead, in the
  cards, the table and one squadron's masthead, while an ``https`` and an ``http`` address do reach
  the ``<img src>``;
* a database without the CreditSystem tables renders the same empty sentence as an empty site, off
  ONE pool connection — the whole list render opens one connection, not two;
* ``squadrons`` is a served nav page, so the menu entry renders.

Every figure is the stub pool's sentinel for the requested scope, so a figure the read never
returned cannot be on the page and a narrowed figure cannot be the whole site's.
"""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import mount, site_pages, sites
from services.webservice.pages import site_squads

#: the two sites every pin builds: the root site (the untagged server) and a group's.
SERVERS_YAML = """\
Root-1:
  port: 10308
Root-2:
  port: 10309
Group-1:
  managed_by:
    - 5th Guards
"""

#: one member per (ucid, server it flew on): name, playtime seconds, takeoffs, kills, deaths, stamp.
MEMBERS: dict[str, dict] = {
    "u-root-1": {"name": "Root One", "server": "Root-1", "playtime": 300 * 3600,
                 "takeoffs": 412, "kills": 813, "deaths": 300,
                 "last_seen": "2026-11-20T10:00:00+00:00"},
    "u-root-2": {"name": "Root Two", "server": "Root-2", "playtime": 100 * 3600,
                 "takeoffs": 141, "kills": 88, "deaths": 80,
                 "last_seen": "2026-11-19T08:30:00+00:00"},
    "u-root-3": {"name": "Root Three", "server": "Root-1", "playtime": 50 * 3600,
                 "takeoffs": 74, "kills": 0, "deaths": 0,
                 "last_seen": "2026-11-01T21:00:00+00:00"},
    "u-root-4": {"name": "Root Four", "server": "Root-2", "playtime": 20 * 3600,
                 "takeoffs": 30, "kills": 10, "deaths": 20,
                 "last_seen": "2026-11-02T21:00:00+00:00"},
    "u-root-5": {"name": "Root Five", "server": "Root-2", "playtime": 10 * 3600,
                 "takeoffs": 12, "kills": 4, "deaths": 8,
                 "last_seen": "2026-11-03T21:00:00+00:00"},
    "u-elsewhere": {"name": "Elsewhere", "server": "Group-1", "playtime": 900 * 3600,
                    "takeoffs": 900, "kills": 900, "deaths": 100,
                    "last_seen": "2026-11-21T12:00:00+00:00"},
    "u-group-1": {"name": "Group One", "server": "Group-1", "playtime": 222 * 3600,
                  "takeoffs": 222, "kills": 222, "deaths": 111,
                  "last_seen": "2026-11-18T09:00:00+00:00"},
}

#: the squadrons the stub answers, with the members they hold and the credits those members earned.
SQUADRONS: list[dict] = [
    {"id": 1, "name": "303rd Fighter Wing", "description": "Standing night wing.",
     "image_url": "", "members": ["u-root-1", "u-root-2", "u-elsewhere"],
     "credits": {"u-root-1": 300, "u-root-2": 200, "u-elsewhere": 900}, "rating": 38.42},
    {"id": 2, "name": "Night Owls", "description": "Late-shift transport outfit.",
     "image_url": "", "members": ["u-root-3", "u-root-4", "u-root-5"],
     "credits": {"u-root-3": 60, "u-root-4": 25, "u-root-5": 15}, "rating": None},
    {"id": 3, "name": "Group Only", "description": "Flies on the group's server alone.",
     "image_url": "", "members": ["u-group-1"], "credits": {"u-group-1": 500}, "rating": 30.0},
]

#: the beta :func:`site_reads.squadrons` reads its combined rating with when the competitive plugin
#: is absent from this process; the stub inverts the formula with it.
BETA = 25.0 / 6.0


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
    """A pool that answers the squadron reads for the servers the request is scoped to."""

    def __init__(self, squadrons=None, present=True):
        self.calls: list[tuple[str, dict]] = []
        self.squadrons: list[dict] = list(SQUADRONS if squadrons is None else squadrons)
        #: whether the CreditSystem plugin's tables (and the event stream) exist in this database
        self.present = present
        #: how many pool connections the render under test opened
        self.connections = 0

    def connection(self):
        self.connections += 1
        return _Connection(self)

    def _scope(self, params) -> list[str]:
        return list((params or {}).get("servers") or ())

    def _scoped(self, squadron: dict, servers: list[str]) -> list[str]:
        return [ucid for ucid in squadron["members"]
                if MEMBERS.get(ucid, {}).get("server") in servers]

    def answer(self, statement, params):
        if "to_regclass" in statement:
            return [{"present": self.present}]
        servers = self._scope(params)
        if "FROM members mb" in statement:
            wanted = int((params or {}).get("squadron_id") or 0)
            squadron = next((row for row in self.squadrons if row["id"] == wanted), None)
            if squadron is None:
                return []
            return [{"ucid": ucid, "name": MEMBERS[ucid]["name"],
                     "last_seen": MEMBERS[ucid]["last_seen"],
                     "playtime": MEMBERS[ucid]["playtime"],
                     "takeoffs": MEMBERS[ucid]["takeoffs"],
                     "kills": MEMBERS[ucid]["kills"],
                     "deaths": MEMBERS[ucid]["deaths"]}
                    for ucid in self._scoped(squadron, servers)]
        if "AS campaign_points" in statement:
            return [{"squadron_id": row["id"],
                     "campaign_points": sum(row["credits"].get(ucid, 0)
                                            for ucid in self._scoped(row, servers))}
                    for row in self.squadrons]
        if "LEFT JOIN trueskill" in statement:
            rated = []
            for row in self.squadrons:
                members = len(self._scoped(row, servers))
                if not members or row["rating"] is None:
                    continue
                rated.append({"squadron_id": row["id"], "members": members,
                              "mu_total": members * (row["rating"] + 3.0 * BETA),
                              "sigma_sq_total": 0.0})
            return rated
        if "FROM credits cr" in statement:
            wanted = set((params or {}).get("ucids") or ())
            return [{"ucid": ucid, "points": points}
                    for row in self.squadrons
                    for ucid, points in row["credits"].items()
                    if ucid in wanted and MEMBERS[ucid]["server"] in servers]
        if "FROM missionstats mst" in statement:
            return []
        if "WITH site_players AS" in statement:
            rows = []
            for row in self.squadrons:
                members = self._scoped(row, servers)
                if not members:
                    continue
                rows.append({"id": row["id"], "name": row["name"],
                             "description": row["description"], "image_url": row["image_url"],
                             "members": len(members)})
            return rows
        return []


class StubBus:
    """The in-process service bus: a pool and the live server registry the site reads."""

    def __init__(self, pool=None, servers=None):
        self.apool = pool or StubPool()
        self.servers = _registry(*(servers or default_servers()))


def _server(name, managed_by=None):
    locals_ = {} if managed_by is None else {"managed_by": [managed_by]}
    return types.SimpleNamespace(name=name, locals=locals_, status=None, players={})


def _registry(*servers) -> ThreadSafeDict:
    return ThreadSafeDict({server.name: server for server in servers})


def default_servers():
    return (_server("Root-1"), _server("Root-2"), _server("Group-1", "5th Guards"))


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
    """The two sites every pin builds, with the root site's own keys overridable."""
    root = {"slug": "", "branding": {"title": "Root Site"}}
    root.update(overrides.get("root") or {})
    group = {"slug": "group1", "group_role": "5th Guards",
             "branding": {"title": "5th Guards"}}
    group.update(overrides.get("group1") or {})
    return {"sites": {"root": root, "group1": group}}


def build(config_dir, config=None):
    """The real shell, from the production factory, with the sites configured."""
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=config or site_config())
    return app, registrar


def _cards(text: str) -> bool:
    return 'class="sq-cards"' in text and 'class="sq-card"' in text


def _table(text: str) -> bool:
    return '<table class="tbl">' in text


# ------------------------------------------------------------------- the list: both layouts

@pytest.mark.asyncio
async def test_the_list_answers_a_served_site_and_draws_the_cards_layout(bus, config_dir):
    """A site that stores ``cards`` renders the grid, its figures and the roster link."""
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "cards"}}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert _cards(page.text), "the cards layout drew no card"
    assert not _table(page.text), "the cards layout drew the table as well"
    assert "303rd Fighter Wing" in page.text
    assert "38.42" in page.text, "the rated squadron's figure is not on its card"
    assert f'href="{site_pages.SQUADRON_PATH}/1"' in page.text, "a card does not link to its page"


@pytest.mark.asyncio
async def test_the_list_storing_list_draws_the_table_and_no_card(bus, config_dir):
    """BOTH directions: the table branch must not also draw the grid, and the reverse."""
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert _table(page.text), "the list layout drew no table"
    assert not _cards(page.text), "the list layout drew the cards as well"
    assert '<th class="rk">#</th>' in page.text
    assert 'class="medal m1"' in page.text, "the leader wears no medal"


@pytest.mark.asyncio
async def test_a_site_storing_no_layout_key_renders_the_shipped_default(bus, config_dir):
    """Absent key means cards — the shipped default, not a blank page."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert _cards(page.text), "a site with no layout: key rendered no card"
    assert "303rd Fighter Wing" in page.text


# ------------------------------------------------------------------- the config key's refusals

def test_an_unknown_page_id_in_the_layout_map_is_refused_at_startup(config_dir):
    with pytest.raises(sites.SiteConfigError) as refusal:
        build(config_dir, site_config(root={"layout": {"servers": "cards"}}))
    message = str(refusal.value)
    assert "layout" in message and "servers" in message, message


def test_a_layout_the_page_does_not_offer_is_refused_at_startup(config_dir):
    with pytest.raises(sites.SiteConfigError) as refusal:
        build(config_dir, site_config(root={"layout": {"squadrons": "grid"}}))
    message = str(refusal.value)
    assert "layout" in message and "grid" in message, message


def test_the_parser_reads_the_stored_layout_and_defaults_the_absent_one():
    absent = sites.parse_sites({"root": {}}).root
    assert absent is not None
    assert absent.page_layout("squadrons") == "cards"
    declared = sites.parse_sites({"root": {"layout": {"squadrons": "list"}}}).root
    assert declared is not None
    assert declared.page_layout("squadrons") == "list"
    # the context a page reads carries the same answer
    assert declared.context().page_layout("squadrons") == "list"


# ------------------------------------------------------------------- the list's two controls

def _body(text: str) -> str:
    """The page without its top bar, so a nav label cannot be read as a control."""
    return text.split("</header>", 1)[-1]


def _rank_row(text: str) -> list[str]:
    """The squadron names in the order the table prints them."""
    body = _body(text)
    return re.findall(r'<span class="nm">([^<]+)</span>', body)


@pytest.mark.asyncio
async def test_the_rank_control_reorders_the_list_and_an_unknown_value_falls_back(bus, config_dir):
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        points = await http.get(site_pages.SQUADRON_PATH)
        members = await http.get(f"{site_pages.SQUADRON_PATH}?by=members")
        rated = await http.get(f"{site_pages.SQUADRON_PATH}?by=rating")
        garbage = await http.get(f"{site_pages.SQUADRON_PATH}?by=nonsense")
    # the site's own figures: 303rd 500 points / 2 members / 38.42; Night Owls 100 / 3 / none
    assert _rank_row(points.text) == ["303rd Fighter Wing", "Night Owls"]
    assert _rank_row(members.text) == ["Night Owls", "303rd Fighter Wing"], \
        "the members order is not the member count's"
    # a squadron with no rating never outranks a rated one
    assert _rank_row(rated.text) == ["303rd Fighter Wing", "Night Owls"]
    assert _rank_row(garbage.text) == _rank_row(points.text), \
        "an unknown order did not fall back to campaign points"
    assert '<a href="' in garbage.text
    assert re.search(r'class="on" aria-current="true">Campaign points<', garbage.text), \
        "the control does not show campaign points as the chosen order"


@pytest.mark.asyncio
async def test_the_rank_control_orders_by_rating_with_an_unrated_squadron_last(bus, config_dir):
    """A rated squadron below zero still outranks an UNRATED one, which sorts last."""
    pool = StubPool(squadrons=SQUADRONS + [
        {"id": 4, "name": "Aardvark Wing", "description": "Unrated, but busy.",
         "image_url": "", "members": ["u-root-2"], "credits": {"u-root-2": 9999}, "rating": None},
        {"id": 5, "name": "Rusty Wing", "description": "Rated, and rated low.",
         "image_url": "", "members": ["u-root-2"], "credits": {"u-root-2": 10}, "rating": -2.5}])
    bus.apool = pool
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        points = await http.get(site_pages.SQUADRON_PATH)
        rated = await http.get(f"{site_pages.SQUADRON_PATH}?by=rating")
    assert _rank_row(points.text)[0] == "Aardvark Wing", "campaign points do not lead the default order"
    # every rated squadron first (a NEGATIVE rating included), then the unrated ones by name
    assert _rank_row(rated.text) == ["303rd Fighter Wing", "Rusty Wing", "Aardvark Wing",
                                     "Night Owls"], "an unrated squadron outranked a rated one"


@pytest.mark.asyncio
async def test_the_server_control_narrows_the_figures_and_an_unknown_name_falls_back(bus, config_dir):
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        whole = await http.get(site_pages.SQUADRON_PATH)
        narrowed = await http.get(f"{site_pages.SQUADRON_PATH}?server=Root-1")
        elsewhere = await http.get(f"{site_pages.SQUADRON_PATH}?server=Group-1")
        unknown = await http.get(f"{site_pages.SQUADRON_PATH}?server=Not-Mine")
    # the whole site: 303rd has 2 members and 500 points, Night Owls 1 and 100
    assert ">2<" in _body(whole.text) and "500" in whole.text
    # Root-1 alone: 303rd keeps one member and 300 points; Night Owls keeps its single member
    narrowed_body = _body(narrowed.text)
    assert "300" in narrowed_body, "the narrowed campaign points are not the server's"
    assert "500" not in narrowed_body, "the whole site's figure survived the server scope"
    assert re.search(r'<td class="r">1</td>\s*<td class="r ranked">300</td>', narrowed_body), \
        "the narrowed row does not carry that server's member count and points"
    # a server of ANOTHER site is not this site's: the whole site is rendered
    assert _rank_row(elsewhere.text) == _rank_row(whole.text)
    assert _rank_row(unknown.text) == _rank_row(whole.text)
    assert re.search(r'class="on" aria-current="true">All servers<', unknown.text), \
        "the control does not show the whole site as chosen"


@pytest.mark.asyncio
async def test_the_list_renders_both_control_rows_and_the_cards_layout_renders_neither(bus, config_dir):
    listed, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(listed) as http:
        table = await http.get(f"{site_pages.SQUADRON_PATH}?server=Root-1")
    body = _body(table.text)
    assert "ctlrows" in body, "the list layout drew no control row"
    assert "Rank by" in body and "Server" in body, "one of the two control rows is missing"
    # every link carries the OTHER parameter: a rank link keeps the server, a server link the rank
    assert "by=rating&amp;server=Root-1" in body or "by=rating&server=Root-1" in body
    assert "by=campaign_points&amp;server=Root-2" in body or \
           "by=campaign_points&server=Root-2" in body

    carded, _ = build(config_dir, site_config(root={"layout": {"squadrons": "cards"}}))
    async with asgi_client(carded) as http:
        cards = await http.get(f"{site_pages.SQUADRON_PATH}?by=members&server=Root-1")
    assert "ctlrows" not in _body(cards.text), "the cards layout drew the list's control rows"
    assert "500" in cards.text, "a URL narrowed the cards layout, which has one order"


# ------------------------------------------------------------------- one squadron's page

@pytest.mark.asyncio
async def test_one_squadron_answers_for_this_site_and_the_not_found_page_otherwise(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.SQUADRON_PATH}/1")
        missing = await http.get(f"{site_pages.SQUADRON_PATH}/999")
        foreign = await http.get(f"{site_pages.SQUADRON_PATH}/3")
    assert page.status_code == 200
    assert "303rd Fighter Wing" in page.text
    assert "Squadron · 1 of 2" in page.text, "the masthead does not state the standing"
    assert "500" in page.text, "the campaign points figure is not on the masthead"
    for answer in (missing, foreign):
        assert answer.status_code == 404, "an id this site does not carry answered a page"
        assert site_squads.NOT_FOUND in answer.text


def _roster_names(text: str) -> list[str]:
    """The pilot names the roster TABLE prints, in its own order."""
    body = text.split('class="tbl roster"', 1)[-1].split("</table>", 1)[0]
    cells = re.findall(r'<a class="nm" href="[^"]*">([^<]+)</a>|<span class="nm">([^<]+)</span>', body)
    return [left or right for left, right in cells]


@pytest.mark.asyncio
async def test_the_roster_lists_only_members_who_flew_on_this_site(bus, config_dir):
    """A member of the squadron who only flew on ANOTHER site's server is absent."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.SQUADRON_PATH}/1")
    assert _roster_names(page.text) == ["Root One", "Root Two"], _roster_names(page.text)
    assert "Elsewhere" not in page.text, "a pilot who never flew on this site is on the roster"
    assert "900" not in page.text, "the other site's figures reached this page"
    # the credit split is scoped the same way: 300 and 200, never the 900 earned elsewhere
    assert "300" in page.text and "200" in page.text


# ------------------------------------------------------------------- the empty state

@pytest.mark.asyncio
async def test_an_empty_site_renders_one_sentence_and_no_table_or_grid(bus, config_dir):
    bus.apool = StubPool(squadrons=[])
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert site_squads.EMPTY in page.text
    assert not _cards(page.text) and not _table(page.text), \
        "an empty site drew an empty grid or an empty table"


# ------------------------------------------------------------------- the mark on a public page

#: one squadron per ``image_url`` this page may be handed. Each name starts with its own number, so
#: the monogram a hostile row draws is that number — one look tells a monogram from an image.
MARKS: list[dict] = [
    {"id": 1, "name": "101 Hostile", "image_url": "javascript:alert(1)", "monogram": "101"},
    {"id": 2, "name": "202 Hostile", "image_url": "data:image/png;base64,AA", "monogram": "202"},
    {"id": 3, "name": "303 Hostile", "image_url": "//evil.example/x.png", "monogram": "303"},
    {"id": 4, "name": "404 Hostile", "image_url": "not a url", "monogram": "404"},
    {"id": 5, "name": "505 Marked", "image_url": "https://example.com/a.png", "monogram": None},
    {"id": 6, "name": "606 Marked", "image_url": "http://example.com/a.png", "monogram": None},
]


def mark_rows() -> list[dict]:
    """The mark pins' squadrons, each with one member who flew on the root site."""
    return [{**row, "description": "", "members": ["u-root-1"],
             "credits": {"u-root-1": 10}, "rating": None} for row in MARKS]


def _table_marks(text: str) -> dict[str, str]:
    """Each squadron's mark as the list TABLE prints it — the sqmark's inner HTML, by name."""
    pairs = re.findall(r'<span class="sqmark">(.*?)</span><span class="sq-name">'
                       r'<span class="nm">([^<]+)</span>', _body(text))
    return {name: inner for inner, name in pairs}


def _card_marks(text: str) -> dict[str, str]:
    """Each squadron's mark as the CARDS layout prints it — the sqmark's inner HTML, by name."""
    pairs = re.findall(r'<span class="sqmark lg">(.*?)</span>\s*<div class="nm">([^<]+)</div>', text)
    return {name: inner for inner, name in pairs}


def _drawn(marks: dict[str, str]) -> tuple[list[str], list[str]]:
    """The names that drew the monogram and the names that drew an image, from a mark map."""
    monogram = [name for name, inner in marks.items() if not inner.startswith("<img")]
    imaged = [name for name, inner in marks.items() if inner.startswith("<img")]
    return monogram, imaged


@pytest.mark.asyncio
async def test_a_mark_that_is_not_an_http_address_draws_the_monogram_in_the_table(bus, config_dir):
    """``javascript:``, ``data:``, ``//host`` and a bare string draw the monogram; ``http(s)`` does not."""
    bus.apool = StubPool(squadrons=mark_rows())
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    marks = _table_marks(page.text)
    assert set(marks) == {row["name"] for row in MARKS}, marks
    monogram, imaged = _drawn(marks)
    assert sorted(monogram) == ["101 Hostile", "202 Hostile", "303 Hostile", "404 Hostile"], monogram
    assert sorted(imaged) == ["505 Marked", "606 Marked"], imaged
    for row in MARKS:
        if row["monogram"] is not None:
            assert marks[row["name"]] == row["monogram"], (row["image_url"], marks[row["name"]])
    assert marks["505 Marked"] == '<img src="https://example.com/a.png" alt="">'
    assert marks["606 Marked"] == '<img src="http://example.com/a.png" alt="">'
    # not one character of a rejected value reaches the page
    for value in ("alert(1)", "javascript:", "base64", "evil.example", "not a url"):
        assert value not in page.text, f"'{value}' reached the page"


@pytest.mark.asyncio
async def test_a_mark_that_is_not_an_http_address_draws_the_monogram_in_the_cards(bus, config_dir):
    """The cards branch obeys the same rule as the table: the page holds one validation, not two."""
    bus.apool = StubPool(squadrons=mark_rows())
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "cards"}}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    marks = _card_marks(page.text)
    assert set(marks) == {row["name"] for row in MARKS}, marks
    monogram, imaged = _drawn(marks)
    assert sorted(monogram) == ["101 Hostile", "202 Hostile", "303 Hostile", "404 Hostile"], monogram
    assert sorted(imaged) == ["505 Marked", "606 Marked"], imaged
    for row in MARKS:
        if row["monogram"] is not None:
            assert marks[row["name"]] == row["monogram"], (row["image_url"], marks[row["name"]])
    assert marks["505 Marked"] == '<img src="https://example.com/a.png" alt="">'


@pytest.mark.asyncio
async def test_one_squadrons_masthead_obeys_the_same_mark_rule(bus, config_dir):
    """The detail page's mark is the list's: rejected values draw the monogram, http(s) an image."""
    bus.apool = StubPool(squadrons=mark_rows())
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        hostile = await http.get(f"{site_pages.SQUADRON_PATH}/1")
        marked = await http.get(f"{site_pages.SQUADRON_PATH}/5")
        plain = await http.get(f"{site_pages.SQUADRON_PATH}/6")
    assert hostile.status_code == 200 and marked.status_code == 200 and plain.status_code == 200
    assert '<span class="sqmark lg">101</span>' in hostile.text, \
        "a javascript: mark drew something other than the monogram"
    assert "alert(1)" not in hostile.text
    assert '<span class="sqmark lg"><img src="https://example.com/a.png" alt=""></span>' in marked.text
    assert '<span class="sqmark lg"><img src="http://example.com/a.png" alt=""></span>' in plain.text


# ------------------------------------------------------------------- one connection per render

@pytest.mark.asyncio
async def test_the_list_render_opens_one_pool_connection_and_answers_the_same_page(bus, config_dir):
    """The probe rides the read's own connection: one round trip per render, same HTML."""
    first = StubPool()
    bus.apool = first
    app, _ = build(config_dir, site_config(root={"layout": {"squadrons": "list"}}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert first.connections == 1, f"the list render opened {first.connections} pool connections"
    second = StubPool()
    bus.apool = second
    async with asgi_client(app) as http:
        again = await http.get(site_pages.SQUADRON_PATH)
    assert second.connections == 1
    assert again.text == page.text, "the pool round trips changed the page's HTML"
    assert _rank_row(page.text) == ["303rd Fighter Wing", "Night Owls"]
    assert "500" in page.text and "100" in page.text


@pytest.mark.asyncio
async def test_an_install_without_the_squadron_tables_renders_the_empty_sentence(bus, config_dir):
    """No ``squadrons`` table: the same sentence an empty site renders, off one connection."""
    pool = StubPool(present=False)
    bus.apool = pool
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
    assert page.status_code == 200
    assert site_squads.EMPTY in page.text
    assert not _cards(page.text) and not _table(page.text)
    assert pool.connections == 1, f"the render opened {pool.connections} pool connections"


# ------------------------------------------------------------------- the wiring

def test_the_squadrons_page_is_a_served_nav_page():
    assert "squadrons" in site_pages.SERVED_NAV_IDS
    assert site_pages.SQUADRON_PATH == site_pages.NAV_PATHS["squadrons"]


@pytest.mark.asyncio
async def test_the_menu_entry_renders_only_when_the_site_offers_it(bus, config_dir):
    """The squadrons PAGE is served for every site; its bar entry is the site's own tick."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        unasked = await http.get("/")
    assert f'href="{site_pages.SQUADRON_PATH}"' not in unasked.text, (
        "an unasked bar carries a Squadrons entry")

    app, _ = build(config_dir, config=site_config(root={"nav": ["overview", "squadrons"]}))
    async with asgi_client(app) as http:
        page = await http.get(site_pages.SQUADRON_PATH)
        root = await http.get("/")
    assert f'<a href="{site_pages.SQUADRON_PATH}" class="on" aria-current="page">' in page.text
    assert f'href="{site_pages.SQUADRON_PATH}"' in root.text, \
        "the site's menu carries no Squadrons entry"
