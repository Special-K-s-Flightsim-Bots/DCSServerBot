"""The console layout: chip in the top bar, attention banner, tabs, adaptive log, sorting, search.

Each test drives the real app in-process and reads the rendered HTML, because each is a decision
about what a reader sees: the user chip moved into the top bar; a banner states what is wrong in
words; the tab strip shows the whole fleet at a glance; the log column's placement follows the DATA
(bottom / right / expanded), never the viewport; every column header is a link and the players tab
searches server-side with a GET."""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice import readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.readmodels import views as view_layer
from test_webui_dashboard import (BOT_YAML, Status, _build, _login, _source, instance, node,
                                  player, server)

SIDEBAR = re.compile(r"<aside.*?</aside>", re.S)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


async def _page(app, query: str = "") -> str:
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get(f"{dashboard_page.DASHBOARD_PATH}{query}")
    assert response.status_code == 200, (query, response.status_code)
    return response.text


def _banner(html: str) -> str:
    """The attention banner, sliced out of the page."""
    start = html.index('<div class="banner')
    return html[start:html.index("</div>", html.index("</ul>") if "</ul>" in html[start:] else start)]


# ------------------------------------------------------- 1. the chip is in the top bar

@pytest.mark.asyncio
async def test_the_chip_is_in_the_top_bar_and_not_in_the_sidebar(config_dir):
    """The signed-in state sits beside the status pills, never under the nav."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app)

    assert '<div class="who">' in html
    sidebar = SIDEBAR.search(html).group(0)
    assert 'class="who"' not in sidebar, "the chip must not be in the sidebar"
    top = html[:html.index("</header>")]
    assert '<div class="who">' in top, "the chip must be in the top bar"
 # the control itself is unchanged where it matters: a real form posting a real path
    assert re.search(r'<form[^>]+method="post"[^>]+action="/auth/logout"', top)
    assert "specialk" in _chip(top), "the chip names the signed-in user"


def _chip(top: str) -> str:
    return top[top.index('<div class="who">'):]


@pytest.mark.asyncio
async def test_the_login_page_renders_no_chip(config_dir):
    """An anonymous caller is not a signed-in user: no name, no avatar, no Sign out."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get("/auth/login")
    assert response.status_code == 200
    assert 'class="who"' not in response.text
    assert 'class="signout"' not in response.text


# ------------------------------------------------------- 2. the attention banner

@pytest.mark.asyncio
async def test_a_clean_cluster_renders_the_calm_banner(config_dir, tmp_path):
    """Everything healthy: one calm sentence, and NO alert list at all."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(players=[player()])],
        node_obj=node(instances={"Training": instance()}))
    state = readmodels.overview(app.state.webui_source_provider())
    assert dashboard_page.attention(state)["ok"] is True

    html = await _page(app)
    assert dashboard_page.CALM_MESSAGE in html
    assert 'class="banner calm"' in html
    assert 'class="banner-list"' not in html


@pytest.mark.asyncio
async def test_the_banner_names_the_node_the_server_and_the_error_lines(config_dir, tmp_path):
    """The three problem classes each appear IN WORDS, built from read-model fields."""
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "2026-09-27 10:00:00.000 INFO\tCache warm\n"
        "2026-09-27 10:00:01.000 ERROR\tLua export failed for Training\n"
        "2026-09-27 10:00:02.000 ERROR\tCallback raised\n",
        encoding="utf-8")
    app, _registrar = _build(config_dir)
    source = _source(servers=[server(name="Maverick Ops", status=Status.SHUTDOWN)],
                     nodes={"dcs-master-01": node(instances={}), "dcs-agent-02": None},
                     node_obj=node(instances={}), log_path=log_file)
    app.state.webui_source_provider = lambda: source
    state = readmodels.overview(source)

    banner = dashboard_page.attention(state)
    assert banner["ok"] is False
    text = " ".join(item["text"] for item in banner["alerts"])
    assert "dcs-agent-02" in text, "the node that stopped heartbeating is named"
    assert "Maverick Ops" in text, "the server that is not running is named"
    assert "ERROR" in text and "2" in text, "the ERROR lines in the visible log are counted"

    html = await _page(app)
    assert 'class="banner alert"' in html
    for item in banner["alerts"]:
        assert item["text"] in html, "every alert is rendered verbatim (escaped by Jinja)"


@pytest.mark.asyncio
async def test_the_banner_tracks_the_read_model_fields_not_a_copy(config_dir):
    """Each alert is traceable to ONE read-model field, so a change moves both together."""
    app, _registrar = _build(config_dir)
    source = _source(servers=[server(status=Status.PAUSED)], nodes={"dcs-agent-02": None},
                     node_obj=node(instances={}))
    state = readmodels.overview(source)
    offline = [n for n in state.nodes if not n.online]
    paused = [s for s in state.servers if s.status.word == "PAUSED"]
    assert offline and paused, "floor: the stub really does carry a dead node and a paused server"
    alerts = dashboard_page.attention(state)["alerts"]
    text = " ".join(item["text"] for item in alerts)
    assert "dcs-agent-02" in text and "not heartbeating" in text, "the dead node is named"
    assert "PAUSED".lower() in text.lower() or "paused" in text, "the paused server is named"
 # an empty cluster is calm (the nodes tab is what says "no nodes are registered")
    assert dashboard_page.attention(readmodels.overview(readmodels.EmptySource()))["ok"] is True


# ------------------------------------------------------- 3. the tab strip

@pytest.mark.asyncio
async def test_the_tab_strip_carries_the_counts_and_a_problem_dot(config_dir):
    """Four tabs, the count of each list as a badge, and a dot ONLY on the tabs with a problem."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name="Up", status=Status.RUNNING, players=[player()]),
                 server(name="Down", status=Status.SHUTDOWN)],
        nodes={"dcs-master-01": node(instances={}), "dcs-agent-02": None},
        node_obj=node(instances={}))
    html = await _page(app)
    state = readmodels.overview(app.state.webui_source_provider())

    strip = html[html.index('<nav class="tabs"'):html.index("</nav>", html.index('<nav class="tabs"'))]
    for key in view_layer.TABLE_LABELS:
        assert f'{view_layer.TABLE_LABELS[key]}' in strip
    assert re.search(rf'<span class="badge[^>]*">{state.server_count}</span>', strip)
    assert re.search(rf'<span class="badge[^>]*">{state.player_count}</span>', strip)
 # a problem dot travels with a BADGE, never alone
    dots = strip.count('class="pdot"')
    badges = len(re.findall(r'<span class="badge', strip))
    assert badges == len(view_layer.TABLE_LABELS)
    assert 0 < dots < badges, "some tabs have a problem, not all of them"


@pytest.mark.asyncio
async def test_a_tab_dot_names_a_problem_the_banner_also_names(config_dir):
    """ONE classification: the dots live on the tabs whose rows the banner reports, and nowhere else.

    Both directions are pinned: a tab holding a reported problem carries a dot, and a tab whose rows
    hold a class the banner stays silent about (an instance binding no server) does NOT — the design
    of record's strip dots Servers and Nodes only, and a second definition of "problem" is how a
    page ends up shouting about something it never names.
    """
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name="Down", status=Status.SHUTDOWN)],
        nodes={"dcs-master-01": node(instances={}), "dcs-agent-02": None},
        node_obj=node(instances={"Unbound": instance()}))
    state = readmodels.overview(app.state.webui_source_provider())
    banner = dashboard_page.attention(state)
    text = " ".join(item["text"] for item in banner["alerts"])
    assert "not heartbeating" in text and "down" in text, \
        "floor: the stub really carries the two classes the dots stand for"
    assert "without a server" not in text, "the banner names no instance class"

    html = await _page(app)
    strip = html[html.index('<nav class="tabs"'):html.index("</nav>", html.index('<nav class="tabs"'))]
    dotted = {tab["key"] for tab in dashboard_page.tab_views(state, "servers", {"level": "info"})
              if tab["problem"]}
    assert dotted == {"nodes", "servers"}, "the dot set is exactly the banner's two tab classes"
    assert strip.count('class="pdot"') == 2
    for key in ("instances", "players"):
        assert dashboard_page._tab_problem(state, key) == ""


@pytest.mark.asyncio
async def test_the_tab_link_carries_the_level_and_the_tab(config_dir):
    """A tab change keeps the filter in force: the URL is the state, nothing lives in the DOM."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app, "?level=all&tab=servers")
    assert re.search(r'href="/\?level=all&amp;tab=nodes"', html), \
        "the node tab's link carries the level AND the tab"


@pytest.mark.asyncio
async def test_one_list_is_visible_at_a_time(config_dir):
    """The strip replaced the four stacked cards: exactly ONE table renders, the active tab's."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={"Training": instance()}))
    servers_tab = await _page(app, "?tab=servers")
    instances_tab = await _page(app, "?tab=instances")
    assert servers_tab.count("<table") == 1
    assert instances_tab.count("<table") == 1
    assert "Training" in instances_tab.split("<tbody")[1].split("</tbody>")[0]
    assert "8088" in instances_tab, "the instances tab shows the WebGUI port"
    assert "8088" not in servers_tab.split("<tbody")[1].split("</tbody>")[0], \
        "the WebGUI port is the instances tab's business, not the servers tab's"


@pytest.mark.asyncio
async def test_the_kpi_tiles_are_gone_from_the_page_and_the_template(config_dir):
    """One fact is stated once: the badges carry the figures, the tile row does not exist.

    The retired tiles leave nothing behind either: the rule that styled them is deleted from the
    stylesheet (CSS a page no longer renders is the same trap as a knob nothing reads).
    """
    from pathlib import Path
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app)
    assert 'class="kpi"' not in html
    assert '<div class="v">' not in html
    templates = Path(dashboard_page.__file__).parent.parent / "templates"
    template = (templates / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="kpi"' not in template
    assert not re.search(r'class="v"', template)
    for path in templates.glob("*.html"):
        assert 'class="kpi"' not in path.read_text(encoding="utf-8"), path.name
    sheet = (templates.parent / "static" / "shell.css").read_text(encoding="utf-8")
 # comments stripped: the block that explains WHY the tiles are gone names `.kpi` itself
    rules = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    assert not re.search(r"(?<![\w-])\.kpi(?![\w-])", rules), \
        "the tile CSS still ships although nothing renders a tile"


# ------------------------------------------------------- 4. the log placement rule

def test_the_placement_rule_reads_the_counts(tmp_path):
    """Longest list <= the threshold puts the log BOTTOM; anything longer puts it RIGHT."""
    limit = dashboard_page.LOG_BOTTOM_MAX_ROWS

    def at(count):
        return readmodels.overview(_source(servers=[server(name=f"S{index}")
                                                    for index in range(count)],
                                           node_obj=node(instances={})))

    assert dashboard_page.longest_list(at(1)) == 1
    assert dashboard_page.log_placement(at(1)) == "bottom"
    assert dashboard_page.longest_list(at(limit)) == limit
    assert dashboard_page.log_placement(at(limit)) == "bottom", "the threshold itself is bottom"
    assert dashboard_page.longest_list(at(limit + 1)) == limit + 1
    assert dashboard_page.log_placement(at(limit + 1)) == "right", "one row past it is right"
    assert dashboard_page.log_placement(at(1), "bogus") == "bottom", \
        "an unknown override is not an error: the rule still answers"
    assert dashboard_page.log_placement(at(1), "expanded") == "expanded"


@pytest.mark.asyncio
async def test_the_page_renders_the_placement_it_decided(config_dir):
    """End to end: the class on the container is the rule's answer, and the mode applies it."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name=f"S{index}") for index in range(30)], node_obj=node(instances={}))
    html = await _page(app)
    assert 'class="console log-right"' in html
    assert 'class="console log-bottom"' not in html
    assert "log position" in html and "right" in _log_footer(html)

    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={"Training": instance()}))
    small = await _page(app)
    assert 'class="console log-bottom"' in small
    assert "bottom" in _log_footer(small)


def _log_footer(html: str) -> str:
    start = html.index('class="ft"')
    return html[start:html.index("</div>", start)]


@pytest.mark.asyncio
async def test_expanded_makes_the_log_the_content_area(config_dir):
    """`?log=expanded` renders the log card INSTEAD of the console grid, and the page says so."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={"Training": instance()}))
    html = await _page(app, "?log=expanded")
    assert "log-card expanded" in html
    assert 'class="console ' not in html, "in expanded mode there is no console grid at all"
    assert "expanded" in _log_footer(html)
    assert 'id="state-panel"' not in html
    assert 'id="log-panel"' in html


@pytest.mark.asyncio
async def test_the_log_placement_override_wins_both_ways(config_dir):
    """`?log=bottom` and `?log=right` override the rule in EITHER direction, and junk falls back.

    The override has to work both ways, not only towards the rule's own answer: a 30-row cluster
    computes `right`, so `?log=bottom` is the interesting direction (and a one-row cluster computing
    `bottom` makes `?log=right` the interesting one). An unknown value is not an error.
    """
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name=f"S{index}") for index in range(30)], node_obj=node(instances={}))
    forced_bottom = await _page(app, "?log=bottom")
    assert 'class="console log-bottom"' in forced_bottom, "?log=bottom must beat the rule"
    assert 'class="console log-right"' not in forced_bottom
    assert 'aria-current="true"' in forced_bottom, "the tag in force is marked"

    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={"Training": instance()}))
    assert 'class="console log-bottom"' in await _page(app), "floor: the rule says bottom here"
    forced_right = await _page(app, "?log=right")
    assert 'class="console log-right"' in forced_right, "?log=right must beat the rule"
    assert "right" in _log_footer(forced_right)

 # an invalid override is not an error: the rule answers again
    assert 'class="console log-bottom"' in await _page(app, "?log=nonsense")


@pytest.mark.asyncio
async def test_the_tab_survives_a_live_refresh(config_dir):
    """The tab rides in the URL the shell hands the stream, and the fragment it retrieves is THAT
    tab's table — otherwise the first tick would swap the reader back to the default tab."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={"Training": instance()}))
    html = await _page(app, "?tab=instances")

    stream = re.search(r'data-stream="([^"]+)"', html)
    assert stream, "the live stream URL is not on the page"
    assert "tab=instances" in stream.group(1).replace("&amp;", "&"), \
        "the stream URL must carry the tab the reader is on"

    fragments_match = re.search(r'data-fragments="([^"]+)"', html)
    assert fragments_match, "the fragment URL is not on the page"
    fragments = fragments_match.group(1).replace("&amp;", "&")
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get(fragments)
    assert response.status_code == 200
    panel = response.json()["state-panel"]
    assert "WebGUI port" in panel, "the refreshed fragment is the instances table"
    assert "Training" in panel


# ------------------------------------------------------- 5. sorting + the players search


@pytest.mark.asyncio
async def test_every_column_header_links_and_the_active_one_carries_its_arrow(config_dir):
    """A sortable table: every header is an anchor, one column is marked, and it toggles."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(players=[player()])],
        node_obj=node(instances={"Training": instance()}))
    html = await _page(app, "?sort=players&dir=desc")
    header = html.split("<thead", 1)[1].split("</thead>", 1)[0]
    state = readmodels.overview(app.state.webui_source_provider())
    columns = readmodels.view_state(dashboard_page.DASHBOARD_PATH, _rows(state),
                                    params=readmodels.ViewParams(sort="players", direction="desc"),
                                    carry={"level": "info"}).tables["servers"].columns
    assert len(columns) == header.count("<th"), "one header cell per column, none invented"

    for column in columns:
 # the link is rendered HTML: the ampersands between the query pairs are escaped
        escaped = column.link.replace("&", "&amp;")
        assert f'href="{escaped}"' in header, f"{column.key} has no sort link"
    active = [column for column in columns if column.active]
    assert len(active) == 1 and active[0].key == "players"
    assert f'aria-sort="{active[0].aria_sort}"' in header
    assert active[0].arrow in header
    assert "▼" in header or "▲" in header
 # the toggle: the same column's link asks for the OTHER direction
    assert 'dir=asc' in active[0].link


def _rows(state):
    return {"servers": state.servers, "nodes": state.nodes, "instances": state.instances,
            "players": state.players}


@pytest.mark.asyncio
async def test_the_players_tab_searches_server_side_with_a_get_form(config_dir):
    """A plain GET form, the active query echoed, both counts, and a clear link."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(players=[player("Viper-1", "aaaaaaaa"), player("Maverick", "bbbbbbbb")])],
        node_obj=node(instances={"Training": instance()}))

    html = await _page(app, "?tab=players&q=viper")
    search = html[html.index('class="search"'):]
    assert 'method="get"' in search
    assert 'name="q"' in search
    assert 'value="viper"' in search
    assert "Filter: name or UCID contains viper" in html
    assert "1 of 2 players" in html
    assert re.search(r'href="/\?tab=players"[^>]*>Clear</a>|>Clear</a>', html)
 # searching the UCID works too (the read model matches name OR ucid)
    assert "aaaaaaaa" in (await _page(app, "?tab=players&q=aaaaaaaa"))

 # an unfiltered players tab says the total, not "X of Y"
    plain = await _page(app, "?tab=players")
    assert "Filter:" not in plain
    assert "Viper-1" in plain

 # the search is a GET: no write route exists anywhere in the page module
    source = open(dashboard_page.__file__, encoding="utf-8").read()
    assert "@router.post" not in source


@pytest.mark.asyncio
async def test_hostile_values_render_escaped(config_dir, tmp_path):
    """Every cell is escaped by Jinja; nothing a plugin wrote can close a tag.

    One test, every dynamic cell the console renders: server/node/instance/mission names and the
    player's name and UCID (all four tabs), the status WORD (its ``title`` carries the raw value,
    which is where an unmapped plugin status lands) and the log line's TEXT. Nothing on this page
    is trusted, and a single pinned sweep is what keeps a later cell from being added unescaped.
    """
    evil = '<script>alert("pwn")</script>'
    log_file = tmp_path / "bot.log"
    log_file.write_text(f"2026-09-27 10:00:01.000 ERROR\t{evil}\n", encoding="utf-8")
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name=evil, instance=evil, mission=evil, status=evil,
                        players=[player(evil, ucid=evil)])],
        nodes={evil: node(name=evil, instances={evil: instance(evil)})},
        node_obj=node(instances={evil: instance(evil)}), log_path=log_file)
    for tab in ("servers", "nodes", "instances", "players"):
        rendered = await _page(app, f"?tab={tab}")
        assert "<script>alert" not in rendered, tab
        assert "&lt;script&gt;" in rendered, tab
 # the servers tab carries BOTH remaining cells: the status word's `title` (where an unmapped
 # raw plugin status lands) and the log line's own text (the log card is on every dashboard)
    html = await _page(app, "?tab=servers")
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#34;pwn&#34;)&lt;/script&gt;" in html, \
        "the log line's own text is escaped, not injected"
 # an attribute is a different escaping context, so it gets its own pin
    assert re.search(r'title="&lt;script&gt;alert\((?:&#34;|&quot;)pwn(?:&#34;|&quot;)\)&lt;/script&gt;"',
                     html), "the status raw value is not escaped in the title attribute"
