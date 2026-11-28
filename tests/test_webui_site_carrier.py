"""The public site's carrier pages: the day-grid board, one pilot's record, the trap sheets, the switch.

WHAT THIS SUITE PINS
--------------------
* the switch is the ONLY gate: off (the shipped default) the menu carries no Carrier entry and the
  three addresses answer the site's not-found page without running a read; on, the board renders;
* the board loads ``site-carrier.js`` under the asset version and its band carries the hook that
  script selects on — while the day columns keep the design's order, oldest first and today last;
  the band's opening SCROLL itself is browser behaviour and is proved in a real browser, not here;
* the board draws one cell per day per pilot under a header row of day numbers, a day with three
  passes draws two tiles and prints ``+n``, and a night pass carries the night marker;
* the ranking floor: a pilot under it is listed after the ranked rows, marked unranked, however good
  the average;
* ``?airframe=`` and ``?server=`` narrow the board, and a value that is not this site's falls back to
  the whole board instead of refusing;
* one pilot's record answers for a pilot with traps here and the not-found page for one without;
* the six badges are read over the whole history: a pilot with 90 traps shows ``century`` pending at
  90/100;
* a trap sheet is served to its OWNER alone — an anonymous visitor and a viewer linked to another
  pilot answer the not-found page with NO read of the sheet, and a pass of another site or one with
  no sheet answers it too;
* the viewer's own page carries the Carrier section with its thumbnails, renders no section at all
  when this database has no ``traps`` table, and its own empty sentence for a pilot who has not
  trapped;
* a full page of passes costs a BOUNDED number of connections — the record's totals, its pass page
  and ONE sheet lookup for the page's ids and the viewer's own UCIDs, never a read per thumbnail;
  a pass the viewer does not own answers no row of that lookup and no image from the sheet route;
* ``?carrier_page=`` is parsed forgivingly: a value that is not a positive whole number renders
  page 1 with a 200, a page past the record renders the last one, and the pager points at the right
  neighbours;
* the public record page emits no ``<img>`` and names no sheet address;
* a ``pages:`` map naming an unknown page id, or holding a value that is not a real boolean, is
  refused at startup naming the key.

Every figure is the stub pool's own answer for the requested scope, so a figure the read never
returned cannot be on the page and a narrowed figure cannot be the whole site's.
"""
from __future__ import annotations

import html
import re
import types
from datetime import datetime, timedelta, timezone

import pytest

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import mount, site_pages, site_reads, sites
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import site_carrier

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

SERVERS_YAML = """\
Root-1:
  port: 10308
Root-2:
  port: 10309
Group-1:
  managed_by:
    - 5th Guards
"""

#: the Discord id the signing-in viewer is linked to. The local door's username IS the identity's
#: subject, so an account named with this number is the pilot ``u-root-1``.
DISCORD_ID = 987654321098765432
#: another pilot's Discord id — a signed-in viewer who owns a record, but not the pass under test.
OTHER_DISCORD_ID = 1234509876

PNG = b"\x89PNG\r\n\x1a\n" + b"trap-sheet" * 8

#: today, as the columns store it: naive UTC.
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _trap(ident: int, ucid: str, *, server: str = "Root-1", days_ago: int = 0, hour: int = 20,
          unit: str = "FA-18C_hornet", grade: str = "OK", points: float = 4.0, wire: int = 3,
          night: bool = False, comment: str = "", sheet: bytes | None = None) -> dict:
    """One ``traps`` row, with its timestamp placed *days_ago* UTC days back."""
    return {"id": ident, "ucid": ucid, "server": server,
            "time": (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0,
                                                             microsecond=0),
            "unit_type": unit, "grade": grade, "points": points, "wire": wire, "night": night,
            "comment": comment, "sheet": sheet}


def _own_traps(count: int, ucid: str = "u-root-1") -> list[dict]:
    """*count* of one pilot's passes, each with its own sheet and its own numbered remark."""
    return [_trap(100 + index, ucid, days_ago=index % 20, hour=index % 24, grade="OK",
                  points=4.0, comment=f"pass-{index + 1}", sheet=PNG)
            for index in range(count)]


#: THE SITE'S OWN TRAPS. ``u-root-1`` is the viewer's own record and traps five times inside the
#: window (three of them on ONE day, one at night, one a cut); ``u-root-3`` has three perfect passes
#: and therefore the best average on the board while staying under the floor.
TRAPS: list[dict] = [
    _trap(1, "u-root-1", days_ago=1, hour=20, grade="OK", points=4.0, wire=3,
          comment="Fair lineup, good correction.", sheet=PNG),
    _trap(2, "u-root-1", days_ago=1, hour=21, grade="_OK_", points=5.0, wire=3,
          comment="Perfect pass."),
    _trap(3, "u-root-1", days_ago=1, hour=22, grade="WO", points=1.0, wire=None,
          comment="Waved off."),
    _trap(4, "u-root-1", days_ago=2, hour=19, grade="_OK_", points=5.0, wire=2, night=True,
          comment="Night pass. Perfect."),
    _trap(5, "u-root-1", days_ago=3, unit="F-14B", grade="C", points=0.0, wire=None,
          comment="Cut.", sheet=PNG),
    _trap(6, "u-root-2", days_ago=2, grade="(OK)", points=3.0, wire=4, comment="A little low."),
    _trap(7, "u-root-2", days_ago=4, grade="OK", points=4.0, wire=3, comment="Caught the three."),
    _trap(8, "u-root-2", days_ago=5, grade="B", points=2.5, wire=None, comment="Bolter."),
    _trap(9, "u-root-2", days_ago=6, grade="OK", points=4.0, wire=3, comment=""),
    _trap(10, "u-root-2", days_ago=7, grade="OK", points=4.0, wire=3, comment=""),
    _trap(11, "u-root-3", server="Root-2", days_ago=2, grade="_OK_", points=5.0, wire=3),
    _trap(12, "u-root-3", server="Root-2", days_ago=3, grade="_OK_", points=5.0, wire=3),
    _trap(13, "u-root-3", server="Root-2", days_ago=8, grade="_OK_", points=5.0, wire=3),
    # ANOTHER site's pass: it must never reach the root site's board or its sheet route
    _trap(14, "u-elsewhere", server="Group-1", days_ago=1, grade="_OK_", points=5.0, sheet=PNG),
    _trap(15, "u-elsewhere", server="Group-1", days_ago=2, grade="OK", points=4.0),
]

NAMES = {"u-root-1": "Root One", "u-root-2": "Root Two", "u-root-3": "Root Three",
         "u-elsewhere": "Elsewhere"}
#: the Discord link of each pilot, by UCID — the identity a session resolves to.
LINKS = {"u-root-1": DISCORD_ID, "u-root-2": OTHER_DISCORD_ID, "u-root-3": 4242,
         "u-elsewhere": 9999}


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


def _perfect() -> str:
    """The grade the read counts as a perfect pass, from the plugin's own vocabulary."""
    return site_reads._perfect_grade()


def _cut() -> str:
    return site_reads._cut_grade()


def _streak(grades: list[str]) -> int:
    """The longest run of passes without a cut — the same figure the statement aggregates."""
    best = longest = 0
    for grade in grades:
        longest = longest + 1 if grade != _cut() else 0
        best = max(best, longest)
    return best


class StubPool:
    """A pool that answers the carrier reads for the servers the request is scoped to."""

    def __init__(self, traps=None, present=True):
        self.calls: list[tuple[str, dict]] = []
        #: how many pool connections the reads opened — the counter the batching pins assert on
        self.connects = 0
        self.traps: list[dict] = list(TRAPS if traps is None else traps)
        #: whether the GreenieBoard plugin's table exists in this database
        self.present = present

    def connection(self):
        self.connects += 1
        return _Connection(self)

    def _scoped(self, params) -> list[dict]:
        names = list((params or {}).get("servers") or ())
        return [row for row in self.traps if row["server"] in names]

    def answer(self, statement, params):
        params = params or {}
        if "to_regclass" in statement:
            return [{"present": self.present}]
        if "WHERE p.discord_id = %(discord_id)s" in statement:
            wanted = params.get("discord_id")
            return [{"ucid": ucid, "name": NAMES[ucid]} for ucid, link in LINKS.items()
                    if link == wanted]
        rows = self._scoped(params)
        if "AS window_traps" in statement:
            wanted_airframe = params.get("airframe")
            if wanted_airframe:
                rows = [row for row in rows if row["unit_type"] == wanted_airframe]
            since, perfect = params.get("since"), params.get("perfect")
            out = {}
            for row in rows:
                entry = out.setdefault(row["ucid"], {
                    "ucid": row["ucid"], "name": NAMES.get(row["ucid"], ""), "traps": 0,
                    "average_points": 0.0, "perfect": 0, "night": 0, "last_trap": row["time"],
                    "window_traps": 0, "_points": []})
                entry["traps"] += 1
                entry["_points"].append(float(row["points"] or 0))
                entry["perfect"] += int(row["grade"] == perfect)
                entry["night"] += int(bool(row["night"]))
                entry["last_trap"] = max(entry["last_trap"], row["time"])
                entry["window_traps"] += int(row["time"] >= since)
            for entry in out.values():
                entry["average_points"] = (sum(entry.pop("_points")) / entry["traps"]
                                           if entry["traps"] else None)
            return list(out.values())
        if "SELECT DISTINCT t.unit_type" in statement:
            return [{"unit_type": name} for name in sorted({row["unit_type"] for row in rows})]
        if "t.night AS night, t.points AS points" in statement and "AS streak" not in statement:
            since = params.get("since")
            wanted_airframe = params.get("airframe")
            return [{"ucid": row["ucid"], "time": row["time"], "grade": row["grade"],
                     "night": row["night"], "points": row["points"]}
                    for row in rows if row["time"] >= since
                    and (not wanted_airframe or row["unit_type"] == wanted_airframe)]
        if "t.comment AS comment" in statement:
            wanted = set(params.get("ucids") or ())
            since = params.get("since")
            picked = [row for row in rows if (not wanted or row["ucid"] in wanted)
                      and (since is None or row["time"] >= since)]
            picked.sort(key=lambda row: (row["time"], row["id"]), reverse=True)
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or len(picked))
            return [{"ucid": row["ucid"], "id": row["id"], "time": row["time"],
                     "unit_type": row["unit_type"], "grade": row["grade"],
                     "points": row["points"], "wire": row["wire"], "night": row["night"],
                     "comment": row["comment"]} for row in picked[offset:offset + limit]]
        if "AS streak" in statement:
            wanted = set(params.get("ucids") or ())
            mine = sorted((row for row in rows if row["ucid"] in wanted),
                          key=lambda row: (row["time"], row["id"]))
            if not mine:
                return [{"traps": 0, "average_points": None, "perfect": 0, "night": 0,
                         "last_trap": None, "name": "", "wires": 0, "streak": 0}]
            return [{
                "traps": len(mine),
                "average_points": sum(float(row["points"] or 0) for row in mine) / len(mine),
                "perfect": sum(1 for row in mine if row["grade"] == params.get("perfect")),
                "night": sum(1 for row in mine if row["night"]),
                "last_trap": max(row["time"] for row in mine),
                "name": NAMES.get(mine[0]["ucid"], ""),
                "wires": len({row["wire"] for row in mine if row["wire"] in (1, 2, 3, 4)}),
                "streak": _streak([row["grade"] for row in mine]),
            }]
        if "t.id = ANY(%(trap_ids)s::bigint[])" in statement:
            # the page's sheet lookup: the viewer's own passes among the requested ids, in one read
            wanted_ids = set(params.get("trap_ids") or ())
            wanted = set(params.get("ucids") or ())
            assert "t.trapsheet IS NOT NULL" in statement, \
                "the sheet lookup does not exclude a pass with no sheet"
            assert "t.player_ucid = ANY(%(ucids)s::text[])" in statement, \
                "the sheet lookup carries no owner filter"
            return [{"id": row["id"]} for row in rows
                    if row["id"] in wanted_ids and row["ucid"] in wanted
                    and row["sheet"] is not None]
        if "AS has_sheet" in statement:
            wanted = set(params.get("ucids") or ())
            mine = sorted((row for row in rows if row["ucid"] in wanted),
                          key=lambda row: (row["time"], row["id"]), reverse=True)
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or len(mine))
            return [{"id": row["id"], "time": row["time"], "unit_type": row["unit_type"],
                     "grade": row["grade"], "points": row["points"], "wire": row["wire"],
                     "night": row["night"], "comment": row["comment"],
                     "has_sheet": row["sheet"] is not None}
                    for row in mine[offset:offset + limit]]
        if "octet_length" in statement:
            wanted = set(params.get("ucids") or ())
            for row in rows:
                if (row["id"] == params.get("trap_id") and row["ucid"] in wanted
                        and row["sheet"] is not None
                        and len(row["sheet"]) <= params.get("max_bytes", 0)):
                    return [{"sheet": row["sheet"]}]
            return []
        return []


def _server(name, managed_by=None):
    locals_ = {} if managed_by is None else {"managed_by": [managed_by]}
    return types.SimpleNamespace(name=name, locals=locals_, status=None, players={})


def default_servers():
    return (_server("Root-1"), _server("Root-2"), _server("Group-1", "5th Guards"))


class StubBus:
    def __init__(self, pool=None):
        self.apool = pool or StubPool()
        self.servers = ThreadSafeDict({server.name: server for server in default_servers()})


@pytest.fixture
def bus():
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
    """Two sites, both with the Carrier page switched on unless a pin turns it off."""
    root = {"slug": "", "branding": {"title": "Root Site"}, "pages": {"carrier": True}}
    root.update(overrides.get("root") or {})
    group = {"slug": "group1", "group_role": "5th Guards",
             "branding": {"title": "5th Guards"}, "pages": {"carrier": True}}
    group.update(overrides.get("group1") or {})
    return {"sites": {"root": root, "group1": group}}


def door_config(users=None) -> dict:
    """The site's config plus ONE enabled username/password door."""
    config = site_config()
    config.update({
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users or [
                     {"username": str(DISCORD_ID), "roles": [],
                      "password_hash": local_auth.hash_password(
                          PASSWORD, iterations=local_auth.MIN_ITERATIONS)}]}},
    })
    return config


def build(config_dir, config=None):
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=config or site_config())
    return app, registrar


def _viewer(ucid: str) -> list[dict]:
    """A local account whose subject is the Discord id *ucid* is linked to."""
    return [{"username": str(LINKS[ucid]), "roles": [],
             "password_hash": local_auth.hash_password(PASSWORD,
                                                       iterations=local_auth.MIN_ITERATIONS)}]


async def _login(client, username: str | None = None) -> None:
    """Sign in through the real form: mint the token, post it, keep the session cookie."""
    page = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
    response = await client.post(auth_routes.LOGIN_PATH,
                                 data={"username": username or str(DISCORD_ID),
                                       "password": PASSWORD, "_csrf_token": token})
    assert response.status_code == 303, "the pin could not sign in"


def _body(text: str) -> str:
    """The document without the top bar, so a nav label cannot be read as a control or a row."""
    return text.split("</header>", 1)[-1]


def _board_names(text: str) -> list[str]:
    """The pilot names the board prints, in its own order."""
    body = text.split('class="tbl cgrid"', 1)[-1].split("</table>", 1)[0]
    return re.findall(r'<a class="nm" href="[^"]*">([^<]+)</a>', body)


def _row_of(text: str, name: str) -> str:
    """The whole ``<tr>`` the board draws for *name*."""
    for row in text.split("<tr", 1)[-1].split("<tr")[1:]:
        if f">{name}</a>" in row:
            return row
    raise AssertionError(f"{name} has no row on the board")


# --------------------------------------------------------------------------- the switch

@pytest.mark.asyncio
async def test_the_switch_off_shows_no_menu_entry_and_answers_the_not_found_page(bus, config_dir):
    """THE DEFAULT. No nav entry, no grid, a 404 — and not one statement is run."""
    app, _ = build(config_dir, site_config(root={"pages": {}}))
    async with asgi_client(app) as http:
        home = await http.get("/")
        before = len(bus.apool.calls)
        page = await http.get(site_pages.CARRIER_PATH)
        record = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
        sheet = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert site_pages.CARRIER_PATH not in home.text, "the menu offers a page that is switched off"
    assert page.status_code == 404
    assert site_carrier.EMPTY in page.text
    assert "cgrid" not in page.text, "a switched-off page drew the grid"
    assert record.status_code == 404 and sheet.status_code == 404
    assert len(bus.apool.calls) == before, "a switched-off page ran a read"


@pytest.mark.asyncio
async def test_a_site_storing_no_pages_map_keeps_the_page_off(bus, config_dir):
    """No ``pages:`` key at all is the shipped default: OFF."""
    config = site_config()
    config["sites"]["root"].pop("pages")
    app, _ = build(config_dir, config)
    async with asgi_client(app) as http:
        home = await http.get("/")
        page = await http.get(site_pages.CARRIER_PATH)
    assert site_pages.CARRIER_PATH not in home.text
    assert page.status_code == 404


@pytest.mark.asyncio
async def test_the_switch_on_renders_the_board_and_its_menu_entry(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        home = await http.get("/")
        page = await http.get(site_pages.CARRIER_PATH)
    assert page.status_code == 200
    assert f'href="{site_pages.CARRIER_PATH}"' in home.text, "the menu carries no Carrier entry"
    assert "cgrid" in page.text, "the board drew no grid"


# --------------------------------------------------------------------------- the grid

def _cells(text: str, name: str) -> list[str]:
    """The day cells of one pilot's row, in the order the grid draws them."""
    row = _row_of(text, name)
    return re.findall(r'<td class="cc"[^>]*>(.*?)</td>', row)


@pytest.mark.asyncio
async def test_the_grid_draws_one_cell_per_day_under_a_header_of_day_numbers(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    header = len(re.findall(r'<th class="cd"', page.text))
    assert header == site_reads.CARRIER_BOARD_DAYS, f"the header row carries {header} day cells"
    assert len(_cells(page.text, "Root One")) == site_reads.CARRIER_BOARD_DAYS
    assert len(_cells(page.text, "Root Two")) == site_reads.CARRIER_BOARD_DAYS
    # the day numbers are the month's own days, oldest first, today last
    numbers = re.findall(r'<th class="cd" title="([^"]+)">(\d+)</th>', page.text)
    assert len(numbers) == site_reads.CARRIER_BOARD_DAYS
    assert numbers[-1][0] == NOW.date().isoformat(), "the grid does not end on today"


@pytest.mark.asyncio
async def test_a_day_of_three_passes_draws_two_tiles_and_the_count(bus, config_dir):
    """Three passes on one day: two tiles, ``+1`` — and a night pass carries the night marker."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    day = (NOW - timedelta(days=1)).date().isoformat()
    row = _row_of(page.text, "Root One")
    cell = re.search(rf'<td class="cc" title="{day}">(.*?)</td>', row)
    assert cell is not None, "that day has no cell"
    inner = cell.group(1)
    assert inner.count('class="ctile') == 2, f"the cell drew {inner.count('class=\"ctile')} tiles"
    assert ">+1<" in inner, f"the cell does not count its third pass: {inner}"
    # the night pass of two days ago wears the deck's black dot
    night = (NOW - timedelta(days=2)).date().isoformat()
    night_cell = re.search(rf'<td class="cc" title="{night}">(.*?)</td>', row)
    assert night_cell is not None
    assert 'class="ctile n"' in night_cell.group(1), "a night pass carries no night marker"
    # an empty day is drawn faint rather than left out
    empty = (NOW - timedelta(days=20)).date().isoformat()
    empty_cell = re.search(rf'<td class="cc" title="{empty}">(.*?)</td>', row)
    assert empty_cell is not None and 'class="ctile none"' in empty_cell.group(1)


@pytest.mark.asyncio
async def test_a_pilot_under_the_ranking_floor_is_listed_last_and_unranked(bus, config_dir):
    """Three perfect passes and the best average never outrank five (or two hundred) real ones."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    names = _board_names(page.text)
    assert names[-1] == "Root Three", f"the under-floor pilot is not last: {names}"
    assert names[:2] == ["Root Two", "Root One"], names
    row = _row_of(page.text, "Root Three")
    assert "unranked" in row, "the under-floor pilot is not marked unranked"
    assert 'class="medal' not in row
    # and the ranked rows are ordered by average points, not by trap count
    assert "3.00" in _row_of(page.text, "Root One")
    assert "3.50" in _row_of(page.text, "Root Two")


# --------------------------------------------------------------------------- the band's opening

def _day_headers(text: str) -> list[tuple[str, str]]:
    """Every day header of the board, as ``(iso date, printed day number)`` in drawn order."""
    return re.findall(r'<th class="cd" title="([^"]+)">(\d+)</th>', text)


@pytest.mark.asyncio
async def test_the_board_loads_its_band_script_with_the_asset_version(bus, config_dir):
    """The page asks for ``site-carrier.js`` in the shell's own form: asset path, ``?v=<version>``,
    ``defer`` — the same URL shape as ``site-live.js`` and ``site-search.js``."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    assert page.status_code == 200
    match = re.search(r'<script src="/static/site-carrier\.js\?v=([^"&]+)" defer></script>',
                      page.text)
    assert match is not None, "the board page does not load site-carrier.js with the asset version"
    assert match.group(1), "the script URL carries an empty asset version"


@pytest.mark.asyncio
async def test_the_band_carries_the_hook_the_script_finds(bus, config_dir):
    """One band, on the board, carrying the attribute ``site-carrier.js`` selects on."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    assert re.search(r'<div class="cband" data-cband-end>', page.text), \
        "the band does not carry the hook site-carrier.js looks for"
    assert page.text.count('class="cband"') == 1, "the board drew more than one band"


@pytest.mark.asyncio
async def test_the_day_columns_run_oldest_first_and_end_on_today(bus, config_dir):
    """The design's order is kept: oldest column left, today last.

    The band's OPENING SCROLL is browser behaviour and no HTTP pin can see it; these are the two
    header values it must land on, and a "fix" that reversed the table fails here.
    """
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
    headers = _day_headers(page.text)
    assert len(headers) == site_reads.CARRIER_BOARD_DAYS, len(headers)
    dates = [date for date, _ in headers]
    assert dates == sorted(dates), f"the day columns are not oldest first: {dates[:3]}"
    assert len(set(dates)) == len(dates), "a day is drawn twice"
    assert dates[0] == (NOW - timedelta(days=site_reads.CARRIER_BOARD_DAYS - 1)).date().isoformat(), \
        f"the first column is not the oldest day: {dates[0]}"
    assert dates[-1] == NOW.date().isoformat(), f"the last column is not today: {dates[-1]}"
    # the printed numbers are the days' own, so the calendar reads left to right
    assert headers[-1][1] == str(NOW.day)


# --------------------------------------------------------------------------- the two controls

@pytest.mark.asyncio
async def test_the_airframe_control_narrows_and_a_foreign_value_falls_back(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        whole = await http.get(site_pages.CARRIER_PATH)
        narrowed = await http.get(f"{site_pages.CARRIER_PATH}?airframe=F-14B")
        unknown = await http.get(f"{site_pages.CARRIER_PATH}?airframe=Bogus")
        foreign = await http.get(f"{site_pages.CARRIER_PATH}?airframe=Su-27")
    assert _board_names(narrowed.text) == ["Root One"], _board_names(narrowed.text)
    assert "Root Two" not in _body(narrowed.text), "the airframe scope kept another pilot"
    assert _board_names(unknown.text) == _board_names(whole.text)
    assert _board_names(foreign.text) == _board_names(whole.text)
    assert re.search(r'class="on" aria-current="true">All airframes<', unknown.text), \
        "the control does not show every airframe as chosen"


@pytest.mark.asyncio
async def test_the_server_control_narrows_and_a_foreign_name_falls_back(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        whole = await http.get(site_pages.CARRIER_PATH)
        narrowed = await http.get(f"{site_pages.CARRIER_PATH}?server=Root-2")
        elsewhere = await http.get(f"{site_pages.CARRIER_PATH}?server=Group-1")
        unknown = await http.get(f"{site_pages.CARRIER_PATH}?server=Not-Mine")
    assert _board_names(narrowed.text) == ["Root Three"], _board_names(narrowed.text)
    assert _board_names(elsewhere.text) == _board_names(whole.text)
    assert _board_names(unknown.text) == _board_names(whole.text)
    assert re.search(r'class="on" aria-current="true">All servers<', unknown.text)


@pytest.mark.asyncio
async def test_the_controls_carry_each_other(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.CARRIER_PATH}?server=Root-2")
    body = html.unescape(_body(page.text))
    # every airframe link keeps the chosen server, and every server link keeps the airframe
    assert "airframe=FA-18C_hornet&server=Root-2" in body
    assert "airframe=all&server=Root-2" in body
    assert "airframe=all&server=Root-1" in body


# --------------------------------------------------------------------------- one pilot's record

@pytest.mark.asyncio
async def test_the_record_answers_for_a_pilot_here_and_the_not_found_page_otherwise(bus, config_dir):
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
        elsewhere = await http.get(f"{site_pages.CARRIER_PATH}/u-elsewhere")
        unknown = await http.get(f"{site_pages.CARRIER_PATH}/u-nobody")
    assert page.status_code == 200
    assert "Root One" in page.text
    assert "Fair lineup, good correction." in page.text, "the LSO's remark is not on the record"
    assert "3.00" in page.text, "the record does not state the pilot's average"
    for answer in (elsewhere, unknown):
        assert answer.status_code == 404, "a pilot with no trap on this site answered a record"
        assert site_carrier.NOT_FOUND in answer.text


@pytest.mark.asyncio
async def test_the_record_draws_no_sheet_and_names_no_sheet_address(bus, config_dir):
    """THE SHEETS ARE THE OWNER'S. A public record carries no image and no link to one."""
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
    assert page.status_code == 200
    assert "<img" not in _body(page.text), "the public record drew an image"
    assert site_pages.CARRIER_TRAP_PATH not in page.text, "the record names the sheet address"


@pytest.mark.asyncio
async def test_the_badges_are_read_over_the_whole_history(bus, config_dir):
    """Ninety passes: ``century`` is PENDING at 90/100, and the other five are earned."""
    traps = [_trap(index, "u-root-1", days_ago=index % 20, grade="OK", points=4.0)
             for index in range(1, 91)]
    bus.apool = StubPool(traps=traps)
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
    assert page.status_code == 200
    century = re.search(r'<span class="badge-card[^"]*"><span class="mk">100</span>'
                        r'<span><b>[^<]+</b> ([^<]*)</span>', page.text)
    assert century is not None, "the century badge is not drawn"
    assert "10 to go" in century.group(1), century.group(1)
    assert "locked" in page.text, "a pending badge is not dimmed"
    first = re.search(r'<span class="mk">1</span><span><b>[^<]+</b> ([^<]*)</span>', page.text)
    assert first is not None and "90" in first.group(1), "the counted badge prints no figure"


# --------------------------------------------------------------------------- the trap sheet

@pytest.mark.asyncio
async def test_the_sheet_answers_its_owner_as_png(bus, config_dir):
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        response = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG


@pytest.mark.asyncio
async def test_an_anonymous_visitor_gets_the_not_found_page_and_no_sheet_read(bus, config_dir):
    app, _ = build(config_dir, door_config())
    async with asgi_client(app) as http:
        response = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert response.status_code == 404
    assert "image/png" not in response.headers.get("content-type", "")
    assert [s for s, _ in bus.apool.calls if "octet_length" in s] == [], \
        "an anonymous request reached the sheet statement"


@pytest.mark.asyncio
async def test_a_viewer_linked_to_another_pilot_reads_no_sheet(bus, config_dir):
    app, _ = build(config_dir, door_config(users=_viewer("u-root-2")))
    async with asgi_client(app) as http:
        await _login(http, str(LINKS["u-root-2"]))
        response = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert response.status_code == 404
    statements = [(statement, params) for statement, params in bus.apool.calls
                  if "octet_length" in statement]
    assert statements, "the sheet statement was never even asked"
    for statement, params in statements:
        assert params["ucids"] == ["u-root-2"], (
            f"another pilot's request queried the owner's sheet: {params['ucids']}")
        assert "t.player_ucid = ANY(%(ucids)s::text[])" in statement, (
            "the sheet statement carries no owner filter: the viewer's UCID is bound but not used")


@pytest.mark.asyncio
async def test_a_foreign_pass_and_a_null_sheet_answer_the_not_found_page(bus, config_dir):
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        foreign = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/14")  # Group-1's pass
        blank = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/2")    # a pass with no sheet
        missing = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/9999")
    for answer in (foreign, blank, missing):
        assert answer.status_code == 404
        assert "image/png" not in answer.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_the_sheet_route_is_switched_off_with_the_page(bus, config_dir):
    app, _ = build(config_dir, site_config(root={"pages": {}}))
    async with asgi_client(app) as http:
        response = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert response.status_code == 404


# --------------------------------------------------------------------------- the viewer's page

@pytest.mark.asyncio
async def test_the_own_page_carries_the_carrier_section_with_its_thumbnails(bus, config_dir):
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        page = await http.get(site_pages.ME_PATH)
    body = html.unescape(_body(page.text))
    assert page.status_code == 200
    assert "Carrier" in body, "the own page carries no Carrier section"
    assert site_carrier.SECTION_EMPTY not in body, "the section says the pilot never trapped"
    assert f'src="{site_pages.CARRIER_TRAP_PATH}/1"' in body, "no trap sheet thumbnail"
    assert "Fair lineup, good correction." in body, "the LSO's remark is not in the list"
    # the thumbnail's own address answers that viewer the image
    async with asgi_client(app) as http:
        await _login(http)
        image = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert image.status_code == 200 and image.content == PNG


@pytest.mark.asyncio
async def test_the_own_page_renders_no_carrier_section_without_the_plugin(bus, config_dir):
    """No ``traps`` table: no heading, no list, no sentence about carrier data."""
    bus.apool = StubPool(present=False)
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        page = await http.get(site_pages.ME_PATH)
    body = _body(page.text)
    assert page.status_code == 200
    assert ">Carrier<" not in body, "the section is drawn without the plugin's table"
    assert site_carrier.SECTION_EMPTY not in body
    assert site_pages.CARRIER_TRAP_PATH not in body


@pytest.mark.asyncio
async def test_a_full_pass_page_costs_a_bounded_number_of_connections(bus, config_dir):
    """A full page of passes is ONE connection's worth of reads, not one per row.

    The record's totals, its pass page and one sheet lookup for the whole page ride on a single
    connection, and the sheet statement binds the page's ids and the viewer's UCIDs once. A read per
    thumbnail would make this figure the page's length; it must not scale with it.
    """
    bus.apool = StubPool(traps=_own_traps(site_reads.CARRIER_TRAP_PAGE_SIZE))
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        before, seen = bus.apool.connects, len(bus.apool.calls)
        page = await http.get(site_pages.ME_PATH)
    assert page.status_code == 200
    body = html.unescape(_body(page.text))
    assert body.count('class="trapthumb"') == site_reads.CARRIER_TRAP_PAGE_SIZE, \
        "the section does not draw one thumbnail cell per pass"
    connects = bus.apool.connects - before
    assert connects <= 5, (f"a page of {site_reads.CARRIER_TRAP_PAGE_SIZE} passes opened "
                           f"{connects} connections")
    sheets = [(statement, params) for statement, params in bus.apool.calls[seen:]
              if "t.id = ANY(%(trap_ids)s::bigint[])" in statement]
    assert len(sheets) == 1, f"the page's sheets were looked up in {len(sheets)} statements"
    assert len(sheets[0][1]["trap_ids"]) == site_reads.CARRIER_TRAP_PAGE_SIZE, \
        "the sheet lookup did not bind the whole page's ids"
    assert sheets[0][1]["ucids"] == ["u-root-1"], "the sheet lookup bound another pilot's UCIDs"


@pytest.mark.asyncio
async def test_a_pass_the_viewer_does_not_own_shows_no_thumbnail(bus, config_dir):
    """The batched lookup keeps the ownership rule: another pilot's pass answers no row.

    Same statement shape as the sheet route — the site's servers, the viewer's own UCIDs, a non-NULL
    sheet — so batching the page cannot hand a reader somebody else's image address.
    """
    app, _ = build(config_dir, door_config(users=_viewer("u-root-2")))
    async with asgi_client(app) as http:
        await _login(http, str(LINKS["u-root-2"]))
        seen = len(bus.apool.calls)
        page = await http.get(site_pages.ME_PATH)
        foreign = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")  # u-root-1's own sheet
    body = html.unescape(_body(page.text))
    assert f'src="{site_pages.CARRIER_TRAP_PATH}/1"' not in body, \
        "the page printed another pilot's sheet address"
    sheets = [(statement, params) for statement, params in bus.apool.calls[seen:]
              if "t.id = ANY(%(trap_ids)s::bigint[])" in statement]
    assert sheets, "the page's sheet presence was never read"
    for statement, params in sheets:
        assert params["ucids"] == ["u-root-2"], f"the lookup bound {params['ucids']}"
        assert "t.player_ucid = ANY(%(ucids)s::text[])" in statement, \
            "the batched sheet statement carries no owner filter"
        assert "t.trapsheet IS NOT NULL" in statement
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_a_carrier_page_that_is_not_a_positive_number_answers_the_first_page(bus, config_dir):
    """``?carrier_page=`` is parsed forgivingly: nothing here may answer a 422 or a refusal."""
    bus.apool = StubPool(traps=_own_traps(60))
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        for value in ("abc", "0", "-3", "2.5", "", "1x"):
            page = await http.get(f"{site_pages.ME_PATH}?carrier_page={value}")
            assert page.status_code == 200, f"?carrier_page={value} answered {page.status_code}"
            body = html.unescape(_body(page.text))
            assert "carrier_page=2" in body, f"?carrier_page={value} did not render page 1"
            assert "carrier_page=1" not in body, f"?carrier_page={value} rendered a later page"


@pytest.mark.asyncio
async def test_the_pager_pages_the_passes_and_clamps_a_page_past_the_record(bus, config_dir):
    """Page 2 holds what page 1 did not, its pager points at its neighbours, and 9 is page 2."""
    bus.apool = StubPool(traps=_own_traps(60))
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        first = await http.get(site_pages.ME_PATH)
        second = await http.get(f"{site_pages.ME_PATH}?carrier_page=2")
        beyond = await http.get(f"{site_pages.ME_PATH}?carrier_page=9")
    one, two, far = (html.unescape(_body(answer.text)) for answer in (first, second, beyond))
    assert one.count('class="trapthumb"') == 50 and two.count('class="trapthumb"') == 10
    assert "carrier_page=1" not in one and "carrier_page=2" in one
    assert "carrier_page=1" in two and "carrier_page=3" not in two
    newest, oldest = (set(re.findall(r"carrier/trap/(\d+)", page)) for page in (one, two))
    assert len(newest) == 50 and len(oldest) == 10, (len(newest), len(oldest))
    assert not (newest & oldest), "the two pages list the same pass"
    # a page past the record answers the last one, with that page's own neighbours
    assert set(re.findall(r"carrier/trap/(\d+)", far)) == oldest
    assert "carrier_page=1" in far and "carrier_page=3" not in far


@pytest.mark.asyncio
async def test_the_own_page_says_so_when_the_pilot_has_not_trapped(bus, config_dir):
    """The plugin is active, the pilot simply has no pass: the section's own sentence, no grid."""
    bus.apool = StubPool(traps=[row for row in TRAPS if row["ucid"] != "u-root-1"])
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        page = await http.get(site_pages.ME_PATH)
    body = html.unescape(_body(page.text))
    assert ">Carrier<" in body, "the section vanished instead of saying it is empty"
    assert site_carrier.SECTION_EMPTY in body
    assert "trapthumb" not in body, "an empty section drew a thumbnail cell"
    assert site_pages.CARRIER_TRAP_PATH not in body


# --------------------------------------------------------------------------- the absent table

@pytest.mark.asyncio
async def test_the_switch_on_without_the_table_renders_one_sentence(bus, config_dir):
    bus.apool = StubPool(present=False)
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
        record = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
    assert page.status_code == 200
    assert site_carrier.EMPTY in page.text
    assert "cgrid" not in page.text, "a grid was drawn without the plugin's table"
    assert "gradekey" not in page.text, "the legend was drawn without the plugin's table"
    assert record.status_code == 404


@pytest.mark.asyncio
async def test_the_board_renders_its_sentence_with_no_plugin_installed(bus, config_dir, monkeypatch):
    """The switch on, the database without the table, AND the plugin's module absent.

    The grade vocabulary lives in the plugin, so a build without that plugin must still answer its
    one sentence instead of raising on the way to the legend.
    """
    import sys

    monkeypatch.setitem(sys.modules, "plugins.greenieboard", None)
    monkeypatch.setitem(sys.modules, "plugins.greenieboard.const", None)
    bus.apool = StubPool(present=False)
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
        record = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
    assert page.status_code == 200
    assert site_carrier.EMPTY in page.text
    assert record.status_code == 404


# --------------------------------------------------------------------------- the config key

def test_an_unknown_page_id_in_the_pages_map_is_refused_at_startup(config_dir):
    with pytest.raises(sites.SiteConfigError) as refusal:
        build(config_dir, site_config(root={"pages": {"squadrons": True}}))
    message = str(refusal.value)
    assert "pages" in message and "squadrons" in message, message


def test_a_value_that_is_not_a_boolean_is_refused_at_startup(config_dir):
    with pytest.raises(sites.SiteConfigError) as refusal:
        build(config_dir, site_config(root={"pages": {"carrier": "true"}}))
    message = str(refusal.value)
    assert "pages.carrier" in message, message


def test_the_parser_reads_the_stored_switch_and_defaults_the_absent_one():
    absent = sites.parse_sites({"root": {}}).root
    assert absent is not None
    assert absent.page_enabled("carrier") is False
    assert absent.context().page_enabled("carrier") is False
    declared = sites.parse_sites({"root": {"pages": {"carrier": True}}}).root
    assert declared is not None
    assert declared.page_enabled("carrier") is True
    assert declared.context().page_enabled("carrier") is True
    # a page that carries no switch is always served
    assert absent.page_enabled("squadrons") is True


def test_the_carrier_page_is_a_declared_and_served_nav_page():
    assert "carrier" in site_pages.SERVED_NAV_IDS
    assert site_pages.CARRIER_PATH == site_pages.NAV_PATHS["carrier"]
    assert site_pages.NAV_LABELS["carrier"] == "Carrier"
    assert site_pages.CARRIER_TRAP_PATH.startswith(site_pages.CARRIER_PATH)


# --------------------------------------------------------------- the plugin gate

@pytest.fixture
def loaded_plugins():
    """State the plugin list for one test and put the module back to UNSTATED afterwards."""
    def state(names):
        site_pages.set_loaded_plugins(names)
        return names
    yield state
    site_pages.set_loaded_plugins(None)


@pytest.mark.asyncio
async def test_the_bar_carries_no_carrier_entry_while_greenieboard_is_unloaded(bus, config_dir,
                                                                              loaded_plugins):
    """The switch alone is not enough: a page whose plugin is not loaded has nothing to link to."""
    loaded_plugins(["os", "mission", "userstats"])
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        home = await http.get("/")
    assert site_pages.CARRIER_PATH not in home.text, (
        "the menu advertises the carrier board with no GreenieBoard plugin loaded")
    assert not site_pages.page_offered("carrier")

    loaded_plugins(["os", "mission", "greenieboard"])
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        home = await http.get("/")
    assert f'href="{site_pages.CARRIER_PATH}"' in home.text, \
        "the loaded plugin's page is still missing from the menu"
    assert site_pages.page_offered("carrier")


def test_an_unstated_plugin_list_offers_every_page(loaded_plugins):
    """Nothing has reported the list: a page a site is showing must not vanish on that account."""
    loaded_plugins(None)
    assert site_pages.page_offered("carrier")
    assert site_pages.page_offered("squadrons")


@pytest.mark.asyncio
async def test_the_switched_on_page_answers_nothing_while_greenieboard_is_unloaded(bus, config_dir,
                                                                                  loaded_plugins):
    """The site's switch cannot conjure data its plugin alone writes: the three addresses 404."""
    loaded_plugins(["os", "mission"])
    app, _ = build(config_dir)
    async with asgi_client(app) as http:
        page = await http.get(site_pages.CARRIER_PATH)
        record = await http.get(f"{site_pages.CARRIER_PATH}/u-root-1")
        sheet = await http.get(f"{site_pages.CARRIER_TRAP_PATH}/1")
    assert page.status_code == 404, "the board answered with its plugin unloaded"
    assert record.status_code == 404 and sheet.status_code == 404
    assert "cgrid" not in page.text, "a page with no plugin behind it drew the grid"
    assert not bus.apool.calls, "a page with no plugin behind it ran a read"


@pytest.mark.asyncio
async def test_the_own_page_draws_no_carrier_band_while_greenieboard_is_unloaded(bus, config_dir,
                                                                                 loaded_plugins):
    """A table an earlier install left behind is not a reason to draw a band with no plugin."""
    loaded_plugins(["os", "mission"])
    app, _ = build(config_dir, door_config(users=_viewer("u-root-1")))
    async with asgi_client(app) as http:
        await _login(http)
        me = await http.get(site_pages.ME_PATH)
    assert me.status_code == 200
    body = html.unescape(_body(me.text))
    assert "Carrier" not in body, (
        "the visitor's own page drew the carrier band with no GreenieBoard plugin loaded")
    assert site_carrier.SECTION_EMPTY not in body
