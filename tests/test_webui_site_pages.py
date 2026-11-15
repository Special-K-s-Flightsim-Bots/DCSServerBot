"""The public site's leaderboard, pilot profile and pilot search.

WHAT THIS SUITE PINS
--------------------
* every figure on the three pages came from ``site_reads``: the stub pool's sentinels are rendered,
  a figure the pool never returned is not, and each read runs exactly ONCE per request;
* the three leaderboard controls are the read's own parameters and each of them is carried in the
  URL, so switching one axis keeps the others;
* the profile renders the pilot's figures and the other UCIDs of the same Discord link — and never
  a Discord id, a clock time or a mission name;
* two sites render their own servers' figures and neither page shows the other's servers;
* an unknown UCID answers ``404`` with copy, an unknown search term answers an empty state with
  copy, and an empty query runs no statement at all;
* the follower pages' paging is the read's own cap, driven by an ``offset``.

The browser measurements (1440/430, scroll width, page heights) are reported on the card; they are
not re-run here.
"""
from __future__ import annotations

import re
import types
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import asgi_client, create_app, install_shell
from core.utils.helper import ThreadSafeDict
from services.webservice import i18n, mount, site_pages, site_reads
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import site_leaderboard

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

#: one Discord id shared by two UCIDs, to exercise the "also known as" list. The value is a
#: SENTINEL: it must never reach the rendered page.
DISCORD_ID = 987654321098765432

#: the pilots the stub answers, with the ONE server each of them flew on. Every figure below is a
#: sentinel in a range no other number on the page uses.
PILOTS: dict[str, dict] = {
    "u-root-1": {"name": "Root One", "server": "Root-1", "discord_id": DISCORD_ID,
                 "playtime": 111 * 3600, "sessions": 1111, "kills": 1113, "pvp": 1115,
                 "deaths": 1117, "teamkills": 0, "takeoffs": 1119, "landings": 1121,
                 "crashes": 1123, "ejections": 1125,
                 "aircraft": [{"aircraft": "F-16C_50", "playtime": 100 * 3600, "sessions": 900}],
                 "recent": [{"hop_on": "2026-11-06T21:12:00+00:00",
                             "hop_off": "2026-11-06T22:12:00+00:00", "slot": "F-16C_50",
                             "server_name": "Root-1", "mission_name": "Secret Mission",
                             "playtime": 3600}]},
    "u-root-1b": {"name": "Root One Alt", "server": "Root-1", "discord_id": DISCORD_ID,
                  "playtime": 0, "sessions": 0, "kills": 0, "pvp": 0, "deaths": 0, "teamkills": 0,
                  "takeoffs": 0, "landings": 0, "crashes": 0, "ejections": 0,
                  "aircraft": [], "recent": []},
    "u-root-2": {"name": "Root Two", "server": "Root-2", "discord_id": -1,
                 "playtime": 11 * 3600, "sessions": 12, "kills": 13, "pvp": 14, "deaths": 15,
                 "teamkills": 0, "takeoffs": 16, "landings": 17, "crashes": 18, "ejections": 19,
                 "aircraft": [], "recent": []},
    "u-group-1": {"name": "Group One", "server": "Group-1", "discord_id": -1,
                  "playtime": 222 * 3600, "sessions": 2222, "kills": 2223, "pvp": 2224,
                  "deaths": 2225, "teamkills": 0, "takeoffs": 2226, "landings": 2227,
                  "crashes": 2228, "ejections": 2229,
                  "aircraft": [{"aircraft": "F-14B", "playtime": 222 * 3600, "sessions": 222}],
                  "recent": [{"hop_on": "2026-11-07T10:00:00+00:00",
                              "hop_off": "2026-11-07T11:00:00+00:00", "slot": "F-14B",
                              "server_name": "Group-1", "mission_name": "Group Mission",
                              "playtime": 3600}]},
}

#: more pilots than either page cap holds, so a full page comes back and the pager has a next link
MANY = [f"Many {index:02d}" for index in range(1, 56)]


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


def _window(rows, params):
    """The rows a statement's LIMIT/OFFSET asks for — the read's own paging, applied by the stub."""
    offset = int((params or {}).get("offset") or 0)
    limit = int((params or {}).get("limit") or len(rows))
    return rows[offset:offset + limit]


class StubPool:
    """A pool whose answers depend on the bound server names — the scope, exercised."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def connection(self):
        return _Connection(self)

    def answer(self, statement, params):
        params = params or {}
        names = list(params.get("servers") or ())

        def on_site(pilot):
            return pilot["server"] in names

        if "WITH page AS MATERIALIZED" in statement:
            rows = [{"ucid": ucid, "name": pilot["name"], "value": pilot["playtime"],
                     "playtime": pilot["playtime"], "sorties": pilot.get("sessions", 0),
                     "takeoffs": pilot.get("takeoffs", 0), "landings": pilot.get("landings", 0),
                     "kills": pilot.get("kills", 0), "pvp": pilot.get("pvp", 0),
                     "crashes": pilot.get("crashes", 0), "ejections": pilot.get("ejections", 0),
                     "aircraft": (pilot.get("aircraft") or [{}])[0].get("aircraft", "")}
                    for ucid, pilot in PILOTS.items() if on_site(pilot)]
            rows += [{"ucid": f"u-many-{index}", "name": name, "value": 60, "playtime": 60,
                      "sorties": 1, "takeoffs": 1, "landings": 1, "kills": 1, "pvp": 0,
                      "crashes": 0, "ejections": 0, "aircraft": ""}
                     for index, name in enumerate(MANY, start=1)]
            return _window(rows, params)
        if "AS kills" in statement:
            ucid = (params.get("ucids") or [""])[0]
            pilot = PILOTS.get(ucid, {})
            return [{key: pilot.get(key, 0) for key in
                     ("playtime", "sessions", "kills", "pvp", "deaths", "teamkills", "takeoffs",
                      "landings", "crashes", "ejections")}]
        if "s.slot AS aircraft" in statement:
            return list(PILOTS.get((params.get("ucids") or [""])[0], {}).get("aircraft") or [])
        if "m.server_name AS server_name" in statement:
            pilot = PILOTS.get((params.get("ucids") or [""])[0], {})
            return ([{"server_name": pilot["server"], "playtime": pilot.get("playtime", 0),
                      "sessions": pilot.get("sessions", 0)}] if pilot else [])
        if "SELECT s.hop_on, s.hop_off" in statement:
            return list(PILOTS.get((params.get("ucids") or [""])[0], {}).get("recent") or [])
        if "SELECT p.ucid, p.name, p.discord_id" in statement:
            pilot = PILOTS.get(params.get("ucid"))
            if pilot is None or not on_site(pilot):
                return []
            return [{"ucid": params["ucid"], "name": pilot["name"],
                     "discord_id": pilot["discord_id"]}]
        if "WHERE p.discord_id = %(discord_id)s" in statement:
            return [{"ucid": ucid, "name": pilot["name"]} for ucid, pilot in PILOTS.items()
                    if pilot["discord_id"] == params.get("discord_id") and on_site(pilot)]
        if "LOWER(p.name) LIKE" in statement:
            wanted = str(params.get("pattern") or "").strip("%").lower()
            rows = [{"ucid": ucid, "name": pilot["name"],
                     "last_seen": (pilot["recent"] or [{}])[0].get("hop_on")}
                    for ucid, pilot in PILOTS.items() if on_site(pilot)
                    and wanted in pilot["name"].lower()]
            rows += [{"ucid": f"u-many-{index}", "name": name,
                      "last_seen": "2026-11-01T00:00:00+00:00"}
                     for index, name in enumerate(MANY, start=1)
                     if wanted in name.lower()]
            return _window(rows, params)
        return []


def _server(name, managed_by=None):
    locals_ = {} if managed_by is None else {"managed_by": [managed_by]}
    return types.SimpleNamespace(name=name, locals=locals_, status=None, players={})


def default_servers():
    return (_server("Root-1"), _server("Root-2"), _server("Group-1", "5th Guards"))


class StubBus:
    def __init__(self):
        self.apool = StubPool()
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


def site_config():
    return {"sites": {"root": {"slug": "", "branding": {"title": "Root Site"},
                               "nav": ["overview", "leaderboard", "squadrons", "players"]},
                      "group1": {"slug": "group1", "group_role": "5th Guards",
                                 "branding": {"title": "5th Guards"},
                                 "nav": ["overview", "leaderboard", "players"]}}}


def build(config_dir, config=None):
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=config or site_config())
    return app, registrar


def door_config(users=None) -> dict:
    """The site's own config plus ONE enabled username/password door.

    Without a door ``sign_in_href`` answers ``None`` and the shell renders no control at all, so the
    sign-in pins need a configuration that actually has a sign-in.
    """
    config = dict(site_config())
    config.update({
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users or [
                     {"username": "admin", "roles": ["Admin"],
                      "password_hash": local_auth.hash_password(
                          PASSWORD, iterations=local_auth.MIN_ITERATIONS)}]}},
    })
    return config


def build_with_door(config_dir):
    return build(config_dir, config=door_config())


async def _login(client) -> None:
    """Sign in through the real form: mint the token, post it, keep the session cookie."""
    page = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
    response = await client.post(auth_routes.LOGIN_PATH,
                                 data={"username": "admin", "password": PASSWORD,
                                       "_csrf_token": token})
    assert response.status_code == 303, "the sign-in pin could not sign in"


def sign_in_controls(markup: str) -> list[str]:
    """The addresses of the shell's top-bar buttons (the door or the console), unescaped."""
    import html

    return [html.unescape(href) for href in
            re.findall(r'<a class="btn btn-sm" href="([^"]*)"', markup)]


def get(app, path):
    """One GET against the real application, in-process."""
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path)

    return asyncio.run(run())


def get_with_cookie(app, path, code, headers=None):
    """One GET carrying the stored language cookie (the console's own ``dcssb_lang``)."""
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            client.cookies.set(i18n.COOKIE_NAME, code)
            return await client.get(path, headers=headers)

    return asyncio.run(run())


def redirect_to(app, path, headers=None):
    """One GET WITHOUT following the redirect: the response and its ``Location``."""
    import asyncio

    async def run():
        async with asgi_client(app) as client:
            return await client.get(path, headers=headers, follow_redirects=False)

    return asyncio.run(run())


# ------------------------------------------------------------------------------- the leaderboard

def test_the_leaderboard_renders_the_reads_rows_and_links_to_their_profiles(config_dir, bus):
    app, _ = build(config_dir)
    response = get(app, "/rankings?metric=playtime&period=all&server=all")
    assert response.status_code == 200
    markup = response.text
    assert "Root One" in markup and "Root Two" in markup
    assert "111" in markup, "the playtime figure (111 h) did not reach the page"
    assert 'href="/player/u-root-1"' in markup, "a row does not link to its pilot's profile"
    assert "Group One" not in markup, "another site's pilot is on the page"
    # the read ran once
    reads = [statement for statement, _params in bus.apool.calls if "WITH page AS MATERIALIZED" in statement]
    assert len(reads) == 1


def test_the_metric_control_reaches_the_read_and_names_itself(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?metric=air_targets").text
    assert "Air Targets" in markup
    statements = [statement for statement, _params in bus.apool.calls if "WITH page AS MATERIALIZED" in statement]
    assert "kills_planes + ms.kills_helicopters" in statements[0], \
        "the chosen metric did not reach the read"
    assert 'href="/rankings?metric=kd_ratio&amp;period=all&amp;server=all"' in markup, \
        "the metric control does not carry the other two axes"


def test_an_unknown_metric_is_the_default_and_the_page_says_which(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?metric=nonsense").text
    assert "Longest Playtimes" in markup
    assert "FROM mv_site_pilot_totals" in [s for s, _p in bus.apool.calls if "WITH page AS MATERIALIZED" in s][0]


@pytest.mark.parametrize("key,clause", [("all", "FROM mv_site_pilot_totals"),
                                        ("30", "interval '1 month'"),
                                        ("90", "interval '3 months'"),
                                        ("365", "interval '1 year'")])
def test_each_period_key_resolves_to_the_bots_own_window(config_dir, bus, key, clause):
    app, _ = build(config_dir)
    get(app, f"/rankings?period={key}")
    statements = [statement for statement, _params in bus.apool.calls if "WITH page AS MATERIALIZED" in statement]
    assert clause in statements[0], f"period '{key}' did not reach the read as {clause}"


def test_the_server_control_narrows_the_read_to_that_server(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?server=Root-2").text
    assert "Root Two" in markup
    assert "Root One" not in markup, "the server control did not re-scope the rows"
    params = [p for s, p in bus.apool.calls if "WITH page AS MATERIALIZED" in s][0]
    assert params["servers"] == ["Root-2"]
    assert 'href="/rankings?metric=playtime&amp;period=all&amp;server=Root-1"' in markup, \
        "the server control does not carry the other two axes"


def test_a_server_of_another_site_is_not_served(config_dir, bus):
    """A root-site URL naming a group's server falls back to the whole site, never to that server."""
    app, _ = build(config_dir)
    markup = get(app, "/rankings?server=Group-1").text
    assert "Group One" not in markup
    params = [p for s, p in bus.apool.calls if "WITH page AS MATERIALIZED" in s][0]
    assert params["servers"] == ["Root-1", "Root-2"]


def test_the_page_cap_and_the_offset_drive_the_pager(config_dir, bus):
    app, _ = build(config_dir)
    first = get(app, "/rankings")
    assert 'href="/rankings?metric=playtime&amp;period=all&amp;server=all&amp;page=2"' in first.text
    assert "Previous" not in first.text.split('class="segments"')[-1], "page 1 offers no Previous"
    second = get(app, "/rankings?page=2")
    params = [p for s, p in bus.apool.calls if "WITH page AS MATERIALIZED" in s][-1]
    assert params["offset"] == site_reads.LEADERBOARD_PAGE_SIZE
    assert 'href="/rankings?metric=playtime&amp;period=all&amp;server=all"' in second.text, \
        "page 2 offers no way back to the first page"


def test_two_sites_show_their_own_leaderboard_rows(config_dir, bus):
    app, _ = build(config_dir)
    root = get(app, "/rankings").text
    group = get(app, "/group1/rankings").text
    assert "Root One" in root and "Group One" not in root
    assert "Group One" in group and "Root One" not in group
    bound = [p["servers"] for s, p in bus.apool.calls if "WITH page AS MATERIALIZED" in s]
    assert bound == [["Root-1", "Root-2"], ["Group-1"]]


def _head(markup: str) -> str:
    return markup.split("<thead>", 1)[1].split("</thead>", 1)[0]


def test_the_table_prints_every_column_in_the_mockups_order(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings").text
    head = _head(markup)
    labels = ["Pilot", "Playtime", "Sorties", "Takeoffs", "Landings", "Kills", "PvP", "Crashes",
              "Ejections", "Most flown"]
    positions = [head.index(label) for label in labels]
    assert positions == sorted(positions), "the columns are not in the mockup's order"
    assert "#" in head.split("</th>", 1)[0], "the rank column is missing"


def test_every_figure_column_is_a_sort_link_and_the_rank_and_airframe_are_not(config_dir, bus):
    app, _ = build(config_dir)
    head = _head(get(app, "/rankings").text)
    for key in ("name", "playtime", "sorties", "takeoffs", "landings", "kills", "pvp", "crashes",
                "ejections"):
        assert f"sort={key}" in head, f"the {key} column is not a sort link"
    assert "sort=aircraft" not in head and "sort=rank" not in head


def test_the_active_column_carries_aria_sort_and_the_arrow(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?metric=playtime&period=all&server=all").text
    head = _head(markup)
    playtime = [cell for cell in re.findall(r"<th[^>]*>.*?</th>", head, re.S)
                if "Playtime" in cell][0]
    assert 'aria-sort="descending"' in playtime
    assert "sorted-desc" in playtime and "↓" in playtime
    kills = [cell for cell in re.findall(r"<th[^>]*>.*?</th>", head, re.S)
             if ">Kills" in cell][0]
    assert "aria-sort" not in kills, "an inactive column must not claim a sort state"
    assert "↕" in kills


def test_sorting_by_a_column_reaches_the_read_and_marks_it(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?sort=kills&direction=desc").text
    statements = [s for s, _p in bus.apool.calls if "WITH page AS MATERIALIZED" in s]
    assert "SUM(ms.kills) AS sort_value" in statements[0]
    kills = [cell for cell in re.findall(r"<th[^>]*>.*?</th>", _head(markup), re.S)
             if ">Kills" in cell][0]
    assert 'aria-sort="descending"' in kills
    # the same header, clicked again, reverses
    assert "sort=kills&amp;direction=asc" in kills


def test_the_controls_keep_the_current_sort_except_the_metric(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?sort=kills&direction=desc").text
    assert 'href="/rankings?metric=playtime&amp;period=all&amp;server=Root-2&amp;sort=kills&amp;direction=desc"' in markup
    assert 'href="/rankings?metric=playtime&amp;period=30&amp;server=all&amp;sort=kills&amp;direction=desc"' in markup
    # a metric change is a new default order: its link drops the explicit sort
    assert 'href="/rankings?metric=kd_ratio&amp;period=all&amp;server=all"' in markup


def test_the_pager_keeps_the_sort_so_page_two_is_the_same_order(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings?sort=ejections&direction=asc").text
    assert 'page=2"' in markup
    page_two = [href for href in re.findall(r'href="([^"]*page=2)"', markup)][0]
    assert "sort=ejections" in page_two and "direction=asc" in page_two


def test_the_wide_row_renders_every_figure_and_the_airframe(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings").text
    for figure in ("111", "1,111", "1,119", "1,121", "1,113", "1,115", "1,123", "1,125"):
        assert figure in markup, f"the figure {figure} did not reach the wide row"
    assert "F-16C_50" in markup, "the favourite airframe did not reach the row"


def test_the_top_three_rows_wear_a_medal_and_the_fourth_does_not(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings").text
    table = markup.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    rows = re.findall(r"<tr[^>]*>.*?</tr>", table, re.S)
    assert len(rows) >= 4
    for position, row in enumerate(rows[:3], start=1):
        assert f'class="medal m{position}"' in row
        assert f'<span class="medal m{position}">{position}</span>' in row
        assert 'class="podium"' in row
    assert "medal" not in rows[3] and "podium" not in rows[3]
    assert '<td class="rk">04</td>' in rows[3]


def test_the_two_unwanted_metrics_are_not_offered_or_named(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/rankings").text
    for gone in ("Most Efficient Killers", "Most Wasteful Pilots",
                 "most_efficient_killers", "most_wasteful_pilots"):
        assert gone not in markup, f"'{gone}' is still on the board"
    assert "Air Targets" in markup and "KD-Ratio" in markup, "the remaining metrics rank"


# ------------------------------------------------------- the ranked column is the metric's own

#: the ranked figure each metric is pinned with, and the value the page must print for it.
RANKED_VALUES: dict[str, tuple[object, str]] = {
    "playtime": (5 * 3600, "5"),
    "air_targets": (41, "41"),
    "ships": (3, "3"),
    "air_defence": (4, "4"),
    "ground_targets": (7, "7"),
    "kd_ratio": (6.4189, "6.42"),
    "pvp_kd_ratio": (4 / 3, "1.33"),
}

#: a figure no ranked cell may show: the row's TOTAL kills, bigger than every ranked value above.
TOTAL_KILLS = 9777


def _row(ucid: str, name: str, value, **figures) -> dict:
    """One read row: the metric's own ``value`` beside every column the table prints."""
    row = {"ucid": ucid, "name": name, "value": value, "aircraft": ""}
    row.update({key: figures.get(key, 0) for key in site_reads.LEADERBOARD_COLUMNS})
    return row


def _answer(metric: str, rows: list[dict], sort: str | None = None,
            direction: str | None = None) -> dict:
    """The read's answer for *metric*, in the read's own vocabulary."""
    order = sort or metric
    return {"metric": metric, "label": site_reads.METRIC_LABELS[metric], "period": "all",
            "sort": order,
            "direction": direction or site_reads.DEFAULT_SORT_DIRECTION.get(order, "desc"),
            "columns": site_reads.LEADERBOARD_COLUMNS, "offset": 0,
            "limit": site_reads.LEADERBOARD_PAGE_SIZE, "rows": rows}


@pytest.fixture
def stub_read(monkeypatch):
    """This page's one read replaced by a stub whose answer a test sets, recording its arguments."""
    state: dict = {"calls": [], "answer": {}}

    async def read(resolved, metric, period, server, page, sort, direction, db):
        state["calls"].append({"metric": metric, "period": period, "server": server,
                               "page": page, "sort": sort, "direction": direction})
        return state["answer"]

    monkeypatch.setattr(site_leaderboard, "read", read)
    return state


def _heads(markup: str) -> list[str]:
    """The table's header cells as plain text, the sort glyphs stripped."""
    return [re.sub(r"[↑↓↕]", "", re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<th[^>]*>.*?</th>", _head(markup), re.S)]


def _body(markup: str) -> list[list[str]]:
    """The table's body rows as lists of cell text."""
    body = markup.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [[re.sub(r"<[^>]+>", "", cell).strip() for cell in
             re.findall(r"<td[^>]*>.*?</td>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>.*?</tr>", body, re.S)]


def _th(markup: str, label: str) -> str:
    """The raw header cell whose text is *label*."""
    return [cell for cell in re.findall(r"<th[^>]*>.*?</th>", _head(markup), re.S)
            if re.sub(r"[↑↓↕]", "", re.sub(r"<[^>]+>", "", cell)).strip() == label][0]


def _ranked_label(metric: str) -> str:
    """The header the ranked column must carry: the metric's own column label, else its msgid."""
    if metric in site_leaderboard.COLUMN_METRICS:
        return [label for label, key, *_rest in site_leaderboard.COLUMNS if key == metric][0]
    return site_leaderboard.METRIC_MSGIDS[metric]


@pytest.mark.parametrize("metric", site_reads.LEADERBOARD_METRICS)
def test_the_ranked_column_shows_the_metrics_own_figure(config_dir, bus, stub_read, metric):
    """Every metric of the vocabulary: the header names it and the cell is the read's own value."""
    value, printed = RANKED_VALUES[metric]
    stub_read["answer"] = _answer(metric, [_row("u-a", "Alpha", value,
                                                playtime=value if metric == "playtime" else 3600,
                                                kills=TOTAL_KILLS)])
    markup = get(build(config_dir)[0], f"/rankings?metric={metric}").text
    heads = _heads(markup)
    index = heads.index(_ranked_label(metric))
    assert "ranked" in _th(markup, _ranked_label(metric)), "the ranked header is not marked"
    assert _body(markup)[0][index].split(" ")[0] == printed
    # the metric the page says it ranks is the read's, and the total kills are not the figure
    assert site_leaderboard.METRIC_MSGIDS[metric] in markup
    assert _body(markup)[0][heads.index("Kills")] == "9,777"


@pytest.mark.parametrize("metric,value", [("ships", 3), ("ground_targets", 7)])
def test_a_pilot_with_more_total_kills_shows_the_ships_or_ground_figure(config_dir, bus, stub_read,
                                                                       metric, value):
    """The reported defect: the ships board showed total kills under the ships header."""
    stub_read["answer"] = _answer(metric, [
        _row("u-a", "Alpha", value, playtime=8 * 3600, kills=TOTAL_KILLS),
        _row("u-b", "Bravo", value + 1, playtime=2 * 3600, kills=11)])
    markup = get(build(config_dir)[0], f"/rankings?metric={metric}").text
    heads = _heads(markup)
    index = heads.index(_ranked_label(metric))
    cells = _body(markup)
    assert [row[index] for row in cells] == [str(value), str(value + 1)]
    assert [row[heads.index("Kills")] for row in cells] == ["9,777", "11"], \
        "the kills column must still print the total"


@pytest.mark.parametrize("metric", sorted(site_leaderboard.RATIO_METRICS))
def test_a_ratio_metric_renders_as_a_ratio(config_dir, bus, stub_read, metric):
    value, printed = RANKED_VALUES[metric]
    stub_read["answer"] = _answer(metric, [_row("u-a", "Alpha", value, kills=TOTAL_KILLS)])
    markup = get(build(config_dir)[0], f"/rankings?metric={metric}").text
    index = _heads(markup).index(_ranked_label(metric))
    assert _body(markup)[0][index] == printed
    assert "." in _body(markup)[0][index], "a ratio must not print as a whole count"
    assert _body(markup)[0][index] != "9,777"


def test_sorting_by_playtime_keeps_the_ships_metric_and_its_values(config_dir, bus, stub_read):
    """The order is not the identity: a column sort re-orders, the board stays the ships board."""
    stub_read["answer"] = _answer("ships", [
        _row("u-a", "Alpha", 3, playtime=9 * 3600, kills=5),
        _row("u-b", "Bravo", 9, playtime=1 * 3600, kills=7)], sort="playtime", direction="desc")
    markup = get(build(config_dir)[0],
                 "/rankings?metric=ships&sort=playtime&direction=desc").text
    heads = _heads(markup)
    index = heads.index("Ships")
    assert index == heads.index(site_leaderboard.METRIC_MSGIDS["ships"])
    assert [row[index] for row in _body(markup)] == ["3", "9"]
    assert [row[heads.index("Playtime")].split(" ")[0] for row in _body(markup)] == ["9", "1"]
    assert 'aria-sort="descending"' in _th(markup, "Playtime")
    ships = _th(markup, "Ships")
    assert "aria-sort" not in ships, "the ranked header must not claim the sort state"
    assert "ranked" in ships
    # the read was asked for the ships board in playtime order
    call = stub_read["calls"][-1]
    assert (call["metric"], call["sort"], call["direction"]) == ("ships", "playtime", "desc")


def test_the_metric_that_is_already_a_column_is_marked_not_duplicated(config_dir, bus, stub_read):
    """Playtime is the table's own column: the board marks it and adds no second column."""
    stub_read["answer"] = _answer("playtime", [_row("u-a", "Alpha", 5 * 3600,
                                                    playtime=5 * 3600, kills=TOTAL_KILLS)])
    markup = get(build(config_dir)[0], "/rankings?metric=playtime").text
    heads = _heads(markup)
    assert heads.count("Playtime") == 1, "the ranked figure is printed twice"
    assert "ranked" in _th(markup, "Playtime")


# ------------------------------------------------------------------------------- the profile

def test_the_profile_renders_totals_airframes_servers_and_the_link(config_dir, bus):
    app, _ = build(config_dir)
    response = get(app, "/player/u-root-1")
    assert response.status_code == 200
    markup = response.text
    for expected in ("Root One", "111", "1,111", "1,113", "1,119", "F-16C_50", "Root-1",
                     "2026-11-06", "Root One Alt", 'href="/player/u-root-1b"'):
        assert expected in markup, f"'{expected}' did not reach the profile"


def test_the_profile_never_renders_a_discord_id_or_a_clock_time(config_dir, bus):
    """A session appears as its DATE, airframe, mission, server and duration — no clock time and
    no Discord id. The mission's name is the site's own rotation, so it is public."""
    app, _ = build(config_dir)
    markup = get(app, "/player/u-root-1").text
    for absent in (str(DISCORD_ID), "21:12", "22:12"):
        assert absent not in markup, f"'{absent}' is on a public profile"
    assert "Secret Mission" in markup, "the recent-sorties table names the mission"


def test_an_unknown_ucid_answers_404_with_copy(config_dir, bus):
    app, _ = build(config_dir)
    response = get(app, "/player/u-nobody")
    assert response.status_code == 404
    assert "No pilot answers to this address" in response.text
    assert "Root One" not in response.text


def test_a_pilot_of_another_site_is_not_found_on_this_one(config_dir, bus):
    app, _ = build(config_dir)
    assert get(app, "/group1/player/u-root-1").status_code == 404
    assert get(app, "/group1/player/u-group-1").status_code == 200


def test_the_profile_is_scoped_to_its_own_site(config_dir, bus):
    app, _ = build(config_dir)
    group = get(app, "/group1/player/u-group-1").text
    assert "222" in group and "Group-1" in group
    assert "Root One" not in group and "Root-1" not in group
    bound = [p["servers"] for s, p in bus.apool.calls if "AS kills" in s]
    assert bound == [["Group-1"]]


def test_no_database_renders_the_empty_state_on_every_page(config_dir):
    app, _ = build(config_dir)
    for path in ("/rankings", "/player/u-root-1", "/players?q=Root"):
        response = get(app, path)
        assert response.status_code == 200, path
        assert "The statistics database is not available yet" in response.text, path


# ------------------------------------------------------------------------------- the search

def test_the_search_matches_a_contains_term_and_links_each_row(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/players?q=Root").text
    assert "Root One" in markup and "Root Two" in markup
    assert 'href="/player/u-root-1"' in markup
    assert "Group One" not in markup
    params = [p for s, p in bus.apool.calls if "LOWER(p.name) LIKE" in s][0]
    assert params["pattern"] == "%root%", "the pattern is not a lower-cased contains term"


def test_a_middle_substring_finds_the_pilot(config_dir, bus):
    """The reported defect: typing the middle of a name matched nothing, so the box read as dead."""
    app, _ = build(config_dir)
    markup = get(app, "/players?q=oot On").text
    assert "Root One" in markup
    assert "Root Two" not in markup, "the term is not being matched anywhere but the start"
    params = [p for s, p in bus.apool.calls if "LOWER(p.name) LIKE" in s][0]
    assert params["pattern"] == "%oot on%"


def test_the_search_escapes_the_term_it_wraps(config_dir, bus):
    """A term carrying a LIKE metacharacter matches it literally, wildcard wrapping included."""
    app, _ = build(config_dir)
    get(app, "/players?q=o%t")
    params = [p for s, p in bus.apool.calls if "LOWER(p.name) LIKE" in s][0]
    assert params["pattern"] == "%o\\%t%"


def test_an_unknown_search_term_is_an_empty_state_not_a_refusal(config_dir, bus):
    app, _ = build(config_dir)
    response = get(app, "/players?q=zzzz")
    assert response.status_code == 200
    assert "No pilot's name contains that" in response.text


def test_an_empty_query_runs_no_statement(config_dir, bus):
    app, _ = build(config_dir)
    response = get(app, "/players")
    assert response.status_code == 200
    assert "No search yet" in response.text
    assert not [s for s, _p in bus.apool.calls if "LOWER(p.name) LIKE" in s]


def test_the_search_page_cap_drives_the_pager(config_dir, bus):
    app, _ = build(config_dir)
    first = get(app, "/players?q=Many")
    assert 'href="/players?q=Many&amp;page=2"' in first.text
    second = get(app, "/players?q=Many&page=2")
    params = [p for s, p in bus.apool.calls if "LOWER(p.name) LIKE" in s][-1]
    assert params["offset"] == site_reads.SEARCH_PAGE_SIZE
    assert 'href="/players?q=Many"' in second.text


# ------------------------------------------------------------------------------- the shell

def test_every_page_carries_the_search_form_pointing_at_its_own_site(config_dir, bus):
    app, _ = build(config_dir)
    root = get(app, "/rankings").text
    group = get(app, "/group1/players?q=Root").text
    assert 'class="search" method="get" action="/players"' in root
    assert 'class="search" method="get" action="/group1/players"' in group
    assert 'name="q"' in root


def test_the_nav_offers_the_pages_that_are_served(config_dir, bus):
    app, _ = build(config_dir)
    markup = get(app, "/").text
    nav = markup.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
    assert ">Leaderboard<" in nav and ">Pilots<" in nav
    assert "/squadrons" not in markup, "the nav offers a page this build does not serve"


def test_the_served_page_ids_are_registered(config_dir, bus):
    app, registrar = build(config_dir)
    owned = {route.path for route in registrar.routes if getattr(route, "path", None)}
    for path in ("/rankings", "/players", "/player/{ucid}",
                 "/group1/rankings", "/group1/players", "/group1/player/{ucid}"):
        assert path in owned, f"{path} is not registered"


def test_the_pages_render_in_the_sites_own_language(config_dir, bus):
    """The site's ``lang`` decides the language of these pages too, from the shipped catalogs."""
    config = site_config()
    config["sites"]["root"]["lang"] = "de"
    app, _ = build(config_dir, config=config)
    assert "Kennzahl" in get(app, "/rankings").text
    assert "Pilotensuche" in get(app, "/players").text
    assert "Auch bekannt als" in get(app, "/player/u-root-1").text


def test_the_stored_language_cookie_beats_the_sites_declared_language(config_dir, bus):
    """The precedence: the visitor's stored choice first, ``site.lang`` second.

    The site declares English, so a German page can only come from the cookie — and ``lang`` has to
    follow the strings, or the document claims a language it is not written in.
    """
    app, _ = build(config_dir)
    response = get_with_cookie(app, "/rankings", "de")
    assert "Kennzahl" in response.text, "the stored cookie did not reach the renderer"
    assert 'lang="de"' in response.text, "the lang attribute disagrees with the strings"


def test_a_german_site_renders_german_without_a_cookie(config_dir, bus):
    """No choice stored: the site's declared language is the answer."""
    config = site_config()
    config["sites"]["root"]["lang"] = "de"
    app, _ = build(config_dir, config=config)
    response = get(app, "/rankings")
    assert "Kennzahl" in response.text
    assert 'lang="de"' in response.text


def test_accept_language_is_not_used_on_the_public_site(config_dir, bus):
    """An English site stays English for a German browser: only the cookie and ``site.lang`` count."""
    app, _ = build(config_dir)
    response = get_with_cookie(app, "/rankings", "xx", headers={"accept-language": "de,en;q=0.5"})
    assert "Longest Playtimes" in response.text
    assert "Kennzahl" not in response.text, "Accept-Language was consulted on the public site"


def test_an_unknown_language_cookie_falls_back_to_the_site_without_an_error(config_dir, bus):
    app, _ = build(config_dir)
    response = get_with_cookie(app, "/rankings", "xx")
    assert response.status_code == 200
    assert "Longest Playtimes" in response.text


def test_the_sites_own_language_switch_is_registered(config_dir, bus):
    app, registrar = build(config_dir)
    routes = {getattr(route, "path", None): getattr(route, "name", None)
              for route in registrar.routes}
    assert routes.get("/group1/language") == "site-language-group1"
    # the root site keeps the installation's fixed /language; no second route is claimed there
    assert [name for path, name in routes.items() if name and name.startswith("site-language-")]
    assert routes.get("/language") == "choose_language"


def test_the_switch_sets_the_cookie_and_returns_to_the_site_path(config_dir, bus):
    app, _ = build(config_dir)
    response = redirect_to(app, "/group1/language?lang=de&next=/group1/players")
    assert response.status_code == 303
    assert response.headers["location"] == "/group1/players"
    cookie = response.headers.get("set-cookie", "")
    assert cookie.startswith(f"{i18n.COOKIE_NAME}=de"), cookie
    for attribute in ("Path=/", "Max-Age=31536000", "SameSite=lax", "HttpOnly"):
        assert attribute in cookie, f"the cookie is missing {attribute}"


def test_an_external_next_lands_on_the_site_root(config_dir, bus):
    """The open redirect this route exists to prevent: an absolute URL is refused."""
    app, _ = build(config_dir)
    response = redirect_to(app, "/group1/language?lang=de&next=https://evil.example/")
    assert response.status_code == 303
    assert response.headers["location"] == "/group1"
    protocol_relative = redirect_to(app, "/group1/language?lang=de&next=//evil.example/")
    assert protocol_relative.headers["location"] == "/group1"


def test_a_console_path_does_not_become_a_site_redirect(config_dir, bus):
    """A site's switch validates against the SITE's tree, never the console's."""
    app, _ = build(config_dir)
    response = redirect_to(app, "/group1/language?lang=de&next=/console/fleet")
    assert response.status_code == 303
    assert response.headers["location"] == "/group1"


def test_the_language_pill_shows_the_language_in_force(config_dir, bus):
    """The pill names the cookie's language and points its rows at the SITE's own switch."""
    app, _ = build(config_dir)
    markup = get_with_cookie(app, "/group1/rankings", "de").text
    assert 'class="langpick"' in markup
    assert "Deutsch" in markup, "the pill does not name the language in force"
    assert 'href="/group1/language?lang=fr&amp;next=/group1/rankings"' in markup


def test_the_console_switch_sets_the_language_the_site_renders(config_dir, bus):
    """One preference, both surfaces: the console's own ``/language`` writes the site's cookie."""
    import asyncio

    app, _ = build(config_dir)

    async def run():
        async with asgi_client(app) as client:
            response = await client.get("/language?lang=de&next=/rankings", follow_redirects=False)
            assert response.status_code == 303
            page = await client.get("/group1/rankings")
            assert "Kennzahl" in page.text, "the site did not read the console's stored choice"

    asyncio.run(run())


def test_the_root_sites_control_uses_the_installation_switch(config_dir, bus):
    """The root site has no second route: its control names the fixed ``/language`` address."""
    app, _ = build(config_dir)
    markup = get_with_cookie(app, "/rankings", "de").text
    assert 'href="/language?lang=fr&amp;next=/rankings"' in markup
    response = redirect_to(app, "/language?lang=de&next=/rankings")
    assert response.status_code == 303 and response.headers["location"] == "/rankings"


def test_the_metric_msgids_are_the_reads_metric_set():
    assert list(site_leaderboard.METRIC_MSGIDS) == list(site_reads.METRIC_LABELS)
    for key, label in site_reads.METRIC_LABELS.items():
        assert site_leaderboard.METRIC_MSGIDS[key] == label


def test_the_page_helpers_format_the_figures_the_bot_reports():
    assert site_leaderboard.figure("playtime", 412 * 3600) == "412"
    assert site_leaderboard.figure("air_targets", 1204) == "1,204"
    assert site_leaderboard.figure("kd_ratio", 6.4189) == "6.42"
    assert site_leaderboard.figure("kd_ratio", None) == "0.00"
    assert site_leaderboard.count(1482) == "1,482"


def test_the_players_page_is_the_search_path_the_nav_declares():
    assert site_pages.SEARCH_PATH == site_pages.NAV_PATHS["players"]
    assert site_pages.page_modules()[1] is site_leaderboard
    assert set(site_pages.SERVED_NAV_IDS) == {"overview", "leaderboard", "players"}


# ----------------------------------------------------------------------------- the sign-in control
#
# A signed-out visitor had no way in at all: the chip renders only for a resolved identity, so the
# top bar carried nothing and "my statistics" was unreachable. The control's target is the door,
# carrying THIS page as the return target, and it is built by ``site_pages.sign_in_href`` — which
# runs the target through ``safe_return``. A hand-built URL here would be the open redirect that
# function exists to prevent.

def test_a_signed_out_visitor_gets_a_sign_in_control_inside_its_own_site(config_dir):
    app, _ = build_with_door(config_dir)
    for path, surface in (("/", "/"), ("/group1/rankings", "/group1")):
        response = get(app, path)
        assert response.status_code == 200
        controls = sign_in_controls(response.text)
        assert len(controls) == 1, f"{path} offers {controls}, not one sign-in control"
        href = controls[0]
        assert href.startswith(f"{auth_routes.LOGIN_PATH}?next="), href
        query = parse_qs(urlsplit(href).query)
        assert query["next"] == [path], "the control does not return the visitor to this page"
        assert query["surface"] == [surface], "the control would return into the wrong surface"
        assert response.headers.get("set-cookie") is None, \
            "a public page mints no cookie: only the login form may set one"


def test_a_signed_out_visitor_gets_no_control_when_the_installation_has_no_door(config_dir, bus):
    """The control follows the configuration: with no auth backend there is nothing to link to."""
    app, _ = build(config_dir)
    markup = get(app, "/").text
    assert sign_in_controls(markup) == []
    assert auth_routes.LOGIN_PATH not in markup


@pytest.mark.asyncio
async def test_a_signed_in_visitor_gets_the_chip_and_the_console_link_instead(config_dir):
    """The console roles come from the bot's own role map, so this needs a ``bot.yaml``."""
    (config_dir / "services").mkdir(exist_ok=True)
    (config_dir / "services" / "bot.yaml").write_text("roles:\n  Admin:\n    - admin\n",
                                                      encoding="utf-8")
    app, _ = build_with_door(config_dir)
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get("/")
    markup = response.text
    assert 'class="who"' in markup, "a signed-in visitor lost the identity chip"
    assert f'href="{site_pages.ME_PATH}"' in markup, "the chip does not link to my statistics"
    console = [href for href in sign_in_controls(markup)
               if href.startswith(mount.CONSOLE_PREFIX)]
    assert console, "a console-role identity is offered no Console link"
    assert auth_routes.LOGIN_PATH not in markup, \
        "a signed-in visitor is offered a sign-in control"


# ----------------------------------------------- the public copy states what a page SHOWS

#: Fragments a public page must not print: a list of what the site withholds reads as an inventory
#: of what it holds.
WITHHELD_FRAGMENTS = (
    "No Discord IDs",
    "no IP address",
    "no ban or watch state",
    "Discord linked",
    "Not linked to a Discord account",
)


def test_the_players_list_keeps_its_short_lead_and_no_withheld_inventory(config_dir, bus):
    """The list closes on ``Public data.`` alone; the rows above it prove the page is real."""
    markup = get(build(config_dir)[0], "/players?q=Root").text
    assert "Public data." in markup, "the players list lost its short lead"
    assert "Root One" in markup, "the players list rendered no row"
    for fragment in WITHHELD_FRAGMENTS:
        assert fragment not in markup, f"{fragment!r} is printed on the players list"


def test_the_public_profile_states_what_it_shows_and_no_link_state(config_dir, bus):
    """``Public profile.`` plus the sentence naming what IS shown, the character tag kept — and no
    tag saying whether the pilot has a Discord account, neither for a linked nor a lone pilot."""
    app, _ = build(config_dir)
    for ucid in ("u-root-1", "u-root-2"):  # linked and not linked
        markup = get(app, f"/player/{ucid}").text
        for fragment in WITHHELD_FRAGMENTS:
            assert fragment not in markup, f"{fragment!r} is printed on {ucid}'s profile"
    markup = get(app, "/player/u-root-1").text
    assert "Public profile." in markup, "the profile lost its short lead"
    assert ("This is what anybody sees for a pilot without signing in: name, squadron, playtime "
            "and the combat figures.") in markup, "the profile lost the sentence naming what it shows"
    tags = re.findall(r'<span class="tag">([^<]*)</span>', markup)
    assert tags == ["PvP and PvE community"], f"the tag row carries more than the character: {tags}"
