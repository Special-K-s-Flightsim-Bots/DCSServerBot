"""The Audit page: the cluster's trail, read-only and Admin only.

One section per acceptance criterion:

* the ROUTE is registered and GATED — ``/audit`` answers 200 for ``Admin`` and 403 for a ``DCS Admin``
  holding no ``audit.view`` (driven through the real application, because a template render would
  prove nothing about ``_core_router()``);
* a row draws the STAMP, the NODE, the ACTOR, the SERVER and the SENTENCE — and a dash where an entry
  written before the actor column existed has none;
* the ORDER opens NEWEST FIRST and a stated order is honoured;
* the FILTERS reach the read (node, server, actor, the event text and a time range) and the page says
  that it narrowed the trail; a date with no time is the WHOLE day, and an unreadable one is IGNORED;
* the PAGER and the SORT LINKS carry the filters, so ordering or paging never silently drops one;
* a read that failed says so in the page's own words and never becomes a 500;
* the page offers NO write — no form posts anywhere.

The read is stubbed at the page's own seam (``pages/audit._read``): the SQL is this page's and is
pinned by nothing but its own parameters here, so what this file pins is the PAGE.
"""
from __future__ import annotations

import datetime
import pathlib

import pytest

from services.webservice.pages import audit as audit_page
from services.webservice.readmodels import audit as audit_model
from test_webui_write_message import build, client, config_dir, sign_in  # noqa: F401
from test_webui_write_pause import clean_registry  # noqa: F401


def _record(*, node="dcs-a-01", actor="[web:local/specialk]", server="SRS-1",
            event="Server started up.", when=None, row_id=1):
    """One row of the read's own answer, in the shape ``dict_row`` hands over."""
    return {"id": row_id, "time": when or datetime.datetime(2026, 11, 2, 21, 16, 15),
            "node": node, "actor": actor, "server_name": server, "event": event,
            "discord_id": None, "ucid": None}


class Read:
    """The page's read seam, recording what the page asked it for."""

    def __init__(self, rows=(), *, total=None, pages=None, page=1, error=None):
        self.rows = list(rows)
        self.total = len(self.rows) if total is None else total
        self.pages = pages or max((self.total + audit_model.PAGE_SIZE - 1) // audit_model.PAGE_SIZE, 1)
        self.page = page
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, *, order_key, direction, page, filters):
        self.calls.append({"order_key": order_key, "direction": direction, "page": page,
                           "filters": filters})
        return {"rows": self.rows, "total": self.total, "page": self.page, "pages": self.pages,
                "per_page": audit_model.PAGE_SIZE, "filters": filters, "error": self.error}

    @property
    def last(self) -> dict:
        assert self.calls, "the page never asked for a read"
        return self.calls[-1]


@pytest.fixture
def stub_read(monkeypatch):
    """Install a recording read; a test may hand its own rows."""
    def install(rows=(), **kwargs) -> Read:
        read = Read(rows, **kwargs)
        monkeypatch.setattr(audit_page, "_read", read)
        return read
    return install


async def _open(http, path=audit_page.AUDIT_PATH, username="specialk"):
    await sign_in(http, username)
    return await http.get(path)


# --------------------------------------------------------------- the route and its door


@pytest.mark.asyncio
async def test_the_audit_page_is_registered_and_admin_only(config_dir, clean_registry, stub_read):
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        allowed = await _open(http, username="specialk")
        assert allowed.status_code == 200, allowed.text[:300]

    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        refused = await _open(http, username="mod1")
        assert refused.status_code == 403, refused.status_code


def _cluster():
    from test_webui_write_message import message_fleet
    return message_fleet()


# --------------------------------------------------------------- what a row shows


@pytest.mark.asyncio
async def test_a_row_shows_the_stamp_the_node_the_actor_the_server_and_the_sentence(
        config_dir, clean_registry, stub_read):
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert "2026-11-02 21:16 UTC" in html
    assert "dcs-a-01" in html
    assert "[web:local/specialk]" in html
    assert "SRS-1" in html
    assert "Server started up." in html


@pytest.mark.asyncio
async def test_an_entry_written_before_the_actor_column_shows_a_dash_not_a_guess(
        config_dir, clean_registry, stub_read):
    stub_read([_record(actor=None, event="Server started up.")])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert "—" in html
    # the sentence is rendered as it was stored: no actor is parsed back out of it
    assert "Server started up." in html


# --------------------------------------------------------------- the order


@pytest.mark.asyncio
async def test_the_trail_opens_newest_first(config_dir, clean_registry, stub_read):
    read = stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        await _open(http)
    assert read.last["order_key"] == "time"
    assert read.last["direction"] == "desc"


@pytest.mark.asyncio
async def test_a_stated_order_is_honoured(config_dir, clean_registry, stub_read):
    read = stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        await _open(http, f"{audit_page.AUDIT_PATH}?sort=node&dir=asc")
    assert read.last["order_key"] == "node"
    assert read.last["direction"] == "asc"


# --------------------------------------------------------------- the filters


@pytest.mark.asyncio
async def test_the_filters_reach_the_read(config_dir, clean_registry, stub_read):
    read = stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http, f"{audit_page.AUDIT_PATH}?node=dcs-a-01&server=SRS-1"
                                  f"&actor=web:local&q=started&since=2026-11-01&until=2026-11-02")).text
    filters = read.last["filters"]
    assert filters.node == "dcs-a-01"
    assert filters.server == "SRS-1"
    assert filters.actor == "web:local"
    assert filters.query == "started"
    assert filters.since == datetime.datetime(2026, 11, 1, 0, 0)
    # a bare date asks for the WHOLE day it names
    assert filters.until == datetime.datetime(2026, 11, 2, 23, 59, 59)
    # and the page says it narrowed the trail rather than reading as the whole of it
    assert "2026-11-01" in html and "2026-11-02" in html


@pytest.mark.asyncio
async def test_an_unreadable_date_is_ignored_rather_than_refused(config_dir, clean_registry,
                                                                 stub_read):
    read = stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        reply = await _open(http, f"{audit_page.AUDIT_PATH}?since=yesterday")
    assert reply.status_code == 200
    assert read.last["filters"].since is None


# --------------------------------------------------------------- paging and links


@pytest.mark.asyncio
async def test_the_pager_and_the_sort_links_carry_the_filters(config_dir, clean_registry,
                                                              stub_read):
    stub_read([_record()], total=250, pages=3)
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http, f"{audit_page.AUDIT_PATH}?node=dcs-a-01")).text
    assert "Page 1 of 3" in html
    # the next link keeps the filter AND asks for page 2
    assert "node=dcs-a-01" in html and "page=2" in html
    # no link leads back from the first page
    assert "Previous" not in html
    # every sort link keeps the filter too
    assert html.count("node=dcs-a-01") >= 4


@pytest.mark.asyncio
async def test_the_pager_offers_no_next_on_the_last_page(config_dir, clean_registry, stub_read):
    stub_read([_record()], total=100, pages=1, page=1)
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert "Next" not in html


# --------------------------------------------------------------- a refused read


@pytest.mark.asyncio
async def test_a_failed_read_says_so_and_never_becomes_a_500(config_dir, clean_registry,
                                                             stub_read):
    stub_read([], error=audit_page.AUDIT_UNAVAILABLE)
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        reply = await _open(http)
    assert reply.status_code == 200
    assert audit_page.AUDIT_UNAVAILABLE in reply.text


# --------------------------------------------------------------- read-only


@pytest.mark.asyncio
async def test_the_page_offers_no_write(config_dir, clean_registry, stub_read):
    """The trail is written by the bot: nothing here posts back to the page, and no row has a control."""
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    # the only form this page owns is the GET filter; nothing posts to /audit
    assert f'action="{audit_page.AUDIT_PATH}"' in html
    assert f'action="{audit_page.AUDIT_PATH}" method="post"' not in html.lower()
    # and the table draws no control column (the shell's Sign out form is the only POST on any page)
    assert 'class="acts"' not in html


# --------------------------------------------------------------- the read itself


class _Cursor:
    """Stand-in for psycopg's cursor: records the statement and replays canned rows."""

    def __init__(self, rows, seen):
        self.rows = rows
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.seen.append((" ".join(statement.split()), params))

    def __aiter__(self):
        async def gen():
            for row in self.rows:
                yield row
        return gen()


class _Conn:
    def __init__(self, rows, seen):
        self.rows = rows
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return _Cursor(self.rows, self.seen)


class _Pool:
    def __init__(self, rows, seen):
        self.rows = rows
        self.seen = seen

    def connection(self):
        return _Conn(self.rows, self.seen)


def _install_pool(monkeypatch, rows):
    seen = []
    monkeypatch.setattr(audit_page, "_pool", lambda: _Pool(rows, seen))
    return seen


@pytest.mark.asyncio
async def test_the_read_binds_its_filters_and_orders_by_the_validated_column(monkeypatch):
    seen = _install_pool(monkeypatch, [dict(_record(), total=1)])
    filters = audit_model.AuditFilters(node="dcs-a-01", actor="local", query="started",
                                       since=datetime.datetime(2026, 11, 1),
                                       until=datetime.datetime(2026, 11, 2, 23, 59, 59))
    answer = await audit_page._read(order_key="time", direction="desc", page=1, filters=filters)
    statement, params = seen[0]
    assert "FROM audit a" in statement
    assert "ORDER BY a.time DESC, a.id DESC" in statement
    assert params["node"] == "dcs-a-01"
    assert params["actor_pattern"] == "%local%"
    assert params["pattern"] == "%started%"
    assert params["limit"] == audit_model.PAGE_SIZE
    assert params["offset"] == 0
    assert answer["total"] == 1
    assert answer["rows"][0]["event"] == "Server started up."


@pytest.mark.asyncio
async def test_a_sort_key_outside_the_allow_list_never_reaches_the_statement(monkeypatch):
    seen = _install_pool(monkeypatch, [dict(_record(), total=1)])
    await audit_page._read(order_key="event; DROP TABLE audit", direction="desc", page=1,
                           filters=audit_model.AuditFilters())
    statement, _ = seen[0]
    assert "DROP TABLE" not in statement
    assert "ORDER BY a.time DESC, a.id DESC" in statement


class _ScriptedConn:
    """A connection whose every cursor answers the NEXT scripted result — one statement each."""

    def __init__(self, script, seen):
        self.script = script
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return _Cursor(self.script.pop(0), self.seen)


class _ScriptedPool:
    def __init__(self, script, seen):
        self.script = list(script)
        self.seen = seen

    def connection(self):
        return _ScriptedConn(self.script, self.seen)


@pytest.mark.asyncio
async def test_a_page_past_the_end_is_answered_with_the_last_page(monkeypatch):
    """The reader typed a number; an empty trail reads as "nothing happened", so go to the last page."""
    seen = []
    rows = [dict(_record(), total=250)]
    # page 9 answers nothing, the COUNT answers 250, the last page answers its rows
    monkeypatch.setattr(audit_page, "_pool",
                        lambda: _ScriptedPool([[], [{"total": 250}], rows], seen))
    answer = await audit_page._read(order_key="time", direction="desc", page=9,
                                    filters=audit_model.AuditFilters())
    assert answer["total"] == 250
    assert answer["pages"] == 3
    assert answer["page"] == 3                 # the LAST page, not the first
    assert len(answer["rows"]) == 1
    assert len(seen) == 3, "empty page, then the count, then the last page"
    assert seen[1][1] == {"node": None, "server": None, "actor": None, "actor_pattern": None,
                          "pattern": None, "since": None, "until": None}
    assert seen[2][1]["offset"] == 200


@pytest.mark.asyncio
async def test_an_empty_trail_lands_on_page_one(monkeypatch):
    seen = []
    # the requested page answers nothing and the COUNT answers zero: there is no last page to go to
    monkeypatch.setattr(audit_page, "_pool", lambda: _ScriptedPool([[], [{"total": 0}]], seen))
    answer = await audit_page._read(order_key="time", direction="desc", page=4,
                                    filters=audit_model.AuditFilters())
    assert answer.get("error") is None, answer.get("error")
    assert answer["page"] == 1
    assert answer["total"] == 0
    assert answer["pages"] == 1


@pytest.mark.asyncio
async def test_no_pool_is_a_typed_refusal_not_a_crash(monkeypatch):
    monkeypatch.setattr(audit_page, "_pool", lambda: None)
    answer = await audit_page._read(order_key="time", direction="desc", page=1,
                                    filters=audit_model.AuditFilters())
    assert answer["error"] == audit_page.AUDIT_NO_BUS


# --------------------------------------------------------------- the filter vocabulary


def test_a_bare_date_is_the_whole_day_it_names():
    assert audit_model.moment("2026-11-02") == datetime.datetime(2026, 11, 2, 0, 0)
    assert audit_model.moment("2026-11-02", end_of_day=True) == datetime.datetime(2026, 11, 2, 23, 59, 59)


def test_a_stamp_with_a_time_is_taken_as_stated():
    assert audit_model.moment("2026-11-02 13:45") == datetime.datetime(2026, 11, 2, 13, 45)
    assert audit_model.moment("2026-11-02T13:45:30") == datetime.datetime(2026, 11, 2, 13, 45, 30)


def test_an_unreadable_moment_is_ignored():
    for value in ("", "yesterday", "2026-13-45", "02.11.2026", None, 7):
        assert audit_model.moment(value) is None, value


def test_a_filter_value_is_trimmed_and_capped():
    filters = audit_model.filters_from({"node": "  " + "n" * 200 + "  ", "unknown": "ignored"})
    assert filters.node == "n" * audit_model.MAX_FILTER_LENGTH
    assert not hasattr(filters, "unknown")


def test_the_summary_says_how_much_of_the_trail_is_shown():
    unfiltered = audit_model.audit_view({"rows": [], "total": 12, "page": 1, "pages": 1})
    assert "12" in audit_model.range_sentence(unfiltered)

    filtered = audit_model.audit_view({
        "rows": [], "total": 3, "page": 1, "pages": 1,
        "filters": audit_model.AuditFilters(node="dcs-a-01")})
    sentence = audit_model.range_sentence(filtered)
    assert "3" in sentence and "filtered" in sentence


# --------------------------------------------------------------- the filter controls


@pytest.mark.asyncio
async def test_node_and_server_are_selects_over_the_names_the_cluster_carries(config_dir,
                                                                             clean_registry,
                                                                             stub_read):
    """A filter nobody can mistype: the choices ARE the nodes and servers the Overview holds."""
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert '<select class="field" id="audit-node" name="node">' in html
    assert '<select class="field" id="audit-server" name="server">' in html
    # the cluster's own names are choices; the fixture fleet carries dcs-a-01 / SRS-1
    assert '<option value="dcs-a-01"' in html
    assert '<option value="SRS-1"' in html
    # and each select opens with the "any" choice, so a filter can always be dropped
    assert '<option value="">' in html


@pytest.mark.asyncio
async def test_the_select_shows_the_filter_in_force(config_dir, clean_registry, stub_read):
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http, f"{audit_page.AUDIT_PATH}?node=dcs-a-01&server=SRS-1")).text
    assert '<option value="dcs-a-01" selected>' in html
    assert '<option value="SRS-1" selected>' in html


def test_a_filter_value_the_list_no_longer_holds_stays_a_choice():
    """A renamed server must not silently widen the view: the value in force leads the list."""
    assert audit_model.select_values(("A", "B"), "GONE") == ("GONE", "A", "B")
    assert audit_model.select_values(("A", "B"), "B") == ("A", "B")
    assert audit_model.select_values((), "") == ()


def test_the_filter_choices_come_from_the_overview_and_are_deduped_and_sorted():
    import types

    state = types.SimpleNamespace(
        nodes=[types.SimpleNamespace(name="n2"), types.SimpleNamespace(name="n1"),
               types.SimpleNamespace(name="n1")],
        servers=[types.SimpleNamespace(name="s2"), types.SimpleNamespace(name="")]
    )
    assert audit_model.filter_options(state) == (("n1", "n2"), ("s2",))
    assert audit_model.filter_options(None) == ((), ())


@pytest.mark.asyncio
async def test_the_range_fields_are_date_pickers(config_dir, clean_registry, stub_read):
    """From and Through are real date controls, not free text: the browser supplies the picker."""
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http, f"{audit_page.AUDIT_PATH}?since=2026-11-01")).text
    assert 'type="date" name="since"' in html
    assert 'type="date" name="until"' in html
    # the value in force is what the picker shows
    assert 'value="2026-11-01"' in html


@pytest.mark.asyncio
async def test_every_filter_control_wears_the_console_field_box(config_dir, clean_registry,
                                                                stub_read):
    """One look for every control: the `.field` class the settings forms and dialogs use."""
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    for name in ("node", "server", "actor", "q", "since", "until"):
        assert f'id="audit-{name}"' in html
        assert f'class="field" id="audit-{name}"' in html, name
    # and no raw Python ever reaches the page (a context key colliding with a dict method once did)
    assert "built-in method" not in html


@pytest.mark.asyncio
async def test_the_stamp_never_wraps_and_the_sentence_does(config_dir, clean_registry, stub_read):
    """A long event must not push 'UTC' onto its own line: the stamp column keeps its width."""
    stub_read([_record(event="x" * 400)])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert '<td class="mono when">' in html
    assert '<td class="col-node">' in html
    assert '<td class="col-actor">' in html

    css = (pathlib.Path(__file__).resolve().parents[1] / 'services' / 'webservice' / 'static'
           / 'shell.css').read_text(encoding='utf-8')
    stamp = [block for block in css.split('}') if 'td.when' in block]
    assert stamp, 'shell.css declares no rule for the stamp column'
    assert any('white-space: nowrap' in block for block in stamp), \
        'the stamp column must never wrap'
    assert any('width: 1%' in block for block in stamp), \
        'the stamp column must not be the column that gives way'


def test_the_stamp_carries_no_clock_it_does_not_have():
    """One line, ends in UTC — the cell the stylesheet keeps on one line is the whole stamp."""
    row = audit_model.audit_rows([_record()])[0]
    assert row.time.endswith("UTC") and "\n" not in row.time


@pytest.mark.asyncio
async def test_a_choice_applies_itself_and_the_text_boxes_still_need_the_button(config_dir,
                                                                               clean_registry,
                                                                               stub_read):
    """A dropdown or a date picker carries a whole decision: letting go of it submits the filter.

    The Filter button stays for the two text boxes and for a browser with JavaScript off, so the form
    is a plain GET either way — the script only adds an early submit, it never replaces the form.
    """
    stub_read([_record()])
    app = build(config_dir, _cluster(), plugins=("mission",))
    async with client(app) as http:
        html = (await _open(http)).text
    assert 'data-autofilter' in html, 'the filter form must declare the auto-apply behaviour'
    assert 'method="get"' in html, 'the fallback path must remain a plain GET'
    assert 'id="audit-since" type="date"' in html
    # the behaviour lives in its own static file (no template carries an inline script but the dialog)
    assert '/static/audit-filter.js' in html
    script = (pathlib.Path(__file__).resolve().parents[1] / 'services' / 'webservice' / 'static'
              / 'audit-filter.js').read_text(encoding='utf-8')
    assert 'addEventListener("change"' in script
    assert '"select, input[type=date]"' in script


def test_the_sort_glyph_never_drops_below_its_word():
    """A narrow column must not push the sort arrow onto a line of its own."""
    css = (pathlib.Path(__file__).resolve().parents[1] / 'services' / 'webservice' / 'static'
           / 'shell.css').read_text(encoding='utf-8')
    rules = [block for block in css.split('}') if '.sort' in block.split('{')[0]]
    assert any('white-space: nowrap' in block for block in rules), \
        'the sort header must keep its label and glyph on one line'
