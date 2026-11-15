"""The console's sorting and search: the ONE validated parameter layer.

``?sort=``, ``?dir=`` and ``?q=`` are USER INPUT arriving in the query string, so the parsing, the
allow-lists and the comparators live in one place
(:mod:`services.webservice.readmodels.views`) and are pinned here as a pure function over the
already-built tuple views. Pins: an unknown sort column, an unknown dir and a malformed ``q`` fall
back to the default, silently and safely, and never reach a rendered link or the state unvalidated;
numeric columns order as numbers, text columns case-insensitively; equal keys order by name stably in
BOTH directions; clicking the active column yields the opposite direction and a new column its natural
one; search matches a partial name AND a partial UCID, reports filtered + total, and an empty query
means no filter; and an injection payload in ``q`` is not echoed into a link unencoded and is escaped
by the environment the template will use. The page <-> live stream integration half is in
``test_webui_view_params.py``."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlparse

from services.webservice.readmodels import views
from services.webservice.readmodels.model import (
    InstanceView, NodeView, PlayerView, ServerView, status_view)

# ------------------------------------------------------------------------------ test doubles


def server_row(name, *, node="dcs-master-01", status="RUNNING", mission=None, elapsed=0,
               players=0):
    """One :class:`ServerView` — the record the Servers table sorts."""
    return ServerView(name=name, node_name=node, instance_name=None, status=status_view(status),
                      port=10309, mission_name=mission, mission_seconds=elapsed, players=players,
                      active_players=players, remote=False)


def node_row(name, *, master=False, online=True, instances=0, servers=0, players=0):
    return NodeView(name=name, master=master, online=online, instances=instances, servers=servers,
                    players=players)


def instance_row(name, *, node="dcs-master-01", server=None, dcs_port=10309, webgui_port=8088):
    return InstanceView(name=name, node_name=node, server_name=server, dcs_port=dcs_port,
                        webgui_port=webgui_port)


def player_row(name, ucid, *, server="Training Caucasus", slot="A-10C · Hog 1"):
    return PlayerView(name=name, ucid=ucid, server_name=server, slot=slot, active=True)


def pairs(link: str) -> dict[str, str]:
    """The query pairs of a produced link, as a dict (order is not part of the contract)."""
    return dict(parse_qsl(urlparse(link).query))


# ------------------------------------------------------- 1. the allow-lists and the fallbacks


def test_the_documented_defaults_are_name_ascending_for_every_table():
    assert views.DEFAULT_SORT == "name"
    assert views.DEFAULT_DIRECTION == "asc"
    for table in views.TABLE_COLUMNS:
        assert views.normalise_sort(table, "") == "name", table
        assert views.normalise_sort(table, None) == "name", table
        assert views.normalise_sort(table, "not-a-column") == "name", table
        assert views.normalise_direction("sideways") == "asc"


def test_an_unknown_sort_column_falls_back_to_the_default_without_reaching_the_state():
    """Junk in ``?sort=`` is refused by the table's own allow-list: the table renders in its
    default order, the raw value appears in NO link, and nothing raises."""
    params = views.view_params({"sort": "nonsense", "dir": "sideways"})
    rows = {"servers": [server_row("Tenth", players=10), server_row("Ninth", players=9)]}
    state = views.view_state("/", rows, params=params)
    table = state.tables["servers"]
    assert (table.sort, table.direction) == ("name", "asc")
    assert [row.name for row in table.rows] == ["Ninth", "Tenth"]
    active = [column.key for column in table.columns if column.active]
    assert active == ["name"], "exactly the default column is marked active"
    assert params.carry() == {}, "an unknown sort/dir is not carried anywhere"
    for column in table.columns:
        assert "nonsense" not in column.link and "sideways" not in column.link


def test_the_sort_allow_list_is_per_table():
    """``?sort=players`` is a Servers/Nodes column and NOT an Instances one — the same request
    sorts one table by it and leaves the other on its default (requirement 1: only the column keys
    of the table in question)."""
    params = views.view_params({"sort": "players", "dir": "desc"})
    state = views.view_state("/", {"servers": [server_row("A")],
                                   "instances": [instance_row("Training")]}, params=params)
    assert state.tables["servers"].sort == "players"
    assert state.tables["servers"].direction == "desc"
    assert state.tables["instances"].sort == "name"
    assert "players" not in [column.key for column in state.tables["instances"].columns]


def test_a_malformed_query_is_trimmed_capped_and_control_stripped():
    assert views.view_params({"q": "  viper\x00\x07 "}).query == "viper"
    assert views.view_params({"q": "\t\n"}).query == ""
    assert len(views.view_params({"q": "x" * 200}).query) == views.MAX_QUERY_LENGTH
    assert views.MAX_QUERY_LENGTH == 64
 # the cap is a cap, not a truncation of something that was already valid
    assert views.view_params({"q": "viper"}).query == "viper"


def test_the_log_position_override_is_a_closed_allow_list():
    for position in views.LOG_POSITIONS:
        assert views.view_params({"log": position}).position == position
    assert views.view_params({"log": "sideways"}).position is None
    assert views.view_params({"log": "<script>"}).position is None
    assert views.view_params({}).position is None


# ------------------------------------------------------------------ 2. the typed comparators


def test_numeric_columns_order_as_numbers_in_both_directions():
    rows = [server_row("Ten", players=10), server_row("Nine", players=9)]
    ascending = views.table_view("/", "servers", rows, sort="players", direction="asc")
    descending = views.table_view("/", "servers", rows, sort="players", direction="desc")
    assert [row.name for row in ascending.rows] == ["Nine", "Ten"], "9 sorts before 10"
    assert [row.name for row in descending.rows] == ["Ten", "Nine"]

 # the elapsed column is numeric too — a plain text sort would put "1000" before "900"
    elapsed = [server_row("Short", elapsed=900), server_row("Long", elapsed=1000)]
    assert [row.name for row in views.table_view("/", "servers", elapsed, sort="elapsed",
                                                 direction="asc").rows] == ["Short", "Long"]


def test_text_columns_order_case_insensitively():
    rows = [server_row("bravo"), server_row("Alpha"), server_row("charlie")]
    ordered = views.table_view("/", "servers", rows, sort="name", direction="asc")
    assert [row.name for row in ordered.rows] == ["Alpha", "bravo", "charlie"]


def test_equal_keys_are_ordered_by_name_in_both_directions():
    """The secondary key is the name, and it is applied on equal primary keys whichever way the
    primary is sorted — so the output is deterministic and never depends on input order."""
    rows = [server_row("Zulu", players=5), server_row("alpha", players=5),
            server_row("Mike", players=5)]
    for direction in ("asc", "desc"):
        ordered = views.table_view("/", "servers", rows, sort="players", direction=direction)
        assert [row.name for row in ordered.rows] == ["alpha", "Mike", "Zulu"], direction
 # and the input order genuinely does not matter
    shuffled = [server_row("Mike", players=5), server_row("Zulu", players=5),
                server_row("alpha", players=5)]
    assert [row.name for row in views.table_view("/", "servers", shuffled, sort="players",
                                                 direction="desc").rows] == \
        ["alpha", "Mike", "Zulu"]


def test_a_missing_mission_never_breaks_the_mission_sort():
    rows = [server_row("With", mission="Caucasus"), server_row("Without", mission=None)]
    assert [row.name for row in views.table_view("/", "servers", rows, sort="mission",
                                                 direction="asc").rows] == ["Without", "With"]


def test_sorting_does_not_touch_the_input_sequence():
    """The read models hand over tuples; the layer must return a new order, not mutate in place."""
    rows = (server_row("B", players=1), server_row("A", players=2))
    views.table_view("/", "servers", rows, sort="players", direction="desc")
    assert [row.name for row in rows] == ["B", "A"]


# --------------------------------------------------------------- 3. the links the template reads


def test_clicking_the_active_column_yields_the_opposite_direction():
    params = views.view_params({"sort": "players", "dir": "desc"})
    state = views.view_state("/", {"servers": [server_row("A", players=1)]}, params=params)
    columns = {column.key: column for column in state.tables["servers"].columns}

    assert pairs(columns["players"].link)["sort"] == "players"
    assert pairs(columns["players"].link)["dir"] == "asc", "the active column flips"
    assert columns["players"].active is True
    assert columns["players"].aria_sort == "descending"
    assert columns["players"].arrow == "▼"

 # a NEW column gets its natural direction: numbers descending, text ascending
    assert pairs(columns["elapsed"].link)["dir"] == "desc"
    assert pairs(columns["name"].link)["dir"] == "asc"
    assert columns["name"].active is False
    assert columns["name"].aria_sort == "none"
    assert columns["name"].arrow == "↕"


def test_the_flip_is_stable_when_the_default_direction_is_in_force():
    """No ``dir`` in the URL: the default (asc) is in force, so the active column's link asks for
    desc — a click toggles rather than re-asking for the state already shown."""
    params = views.view_params({"sort": "name"})
    state = views.view_state("/", {"servers": [server_row("A")]}, params=params)
    columns = {column.key: column for column in state.tables["servers"].columns}
    assert (columns["name"].active, columns["name"].aria_sort) == (True, "ascending")
    assert pairs(columns["name"].link)["dir"] == "desc"


def test_the_sorted_state_is_stated_in_words():
    params = views.view_params({"sort": "players", "dir": "desc"})
    state = views.view_state("/", {"servers": [server_row("A", players=1)]}, params=params)
    assert state.tables["servers"].sorted_label == "Sorted by Players, descending"
    default = views.view_state("/", {"servers": [server_row("A")]})
    assert default.tables["servers"].sorted_label == "Sorted by Server, ascending"


def test_every_column_link_carries_the_validated_query_level_and_position():
    params = views.view_params({"sort": "name", "dir": "asc", "q": "viper", "log": "right"})
    state = views.view_state("/players", {"players": [player_row("Viper-1", "4b1f9c22")]},
                             params=params, carry={"level": "all"})
    link = next(column for column in state.tables["players"].columns
                if column.key == "ucid").link
    query = pairs(link)
    assert query == {"sort": "ucid", "dir": "asc", "q": "viper", "log": "right", "level": "all"}


def test_the_links_carry_only_validated_values():
    """A garbage ``sort``/``dir``/``log`` never reaches a link, and the query is the state's own."""
    params = views.view_params({"sort": "<script>", "dir": "sideways", "log": "nowhere"})
    assert params.carry() == {}, "nothing valid was stated, so nothing is carried"
    state = views.view_state("/", {"servers": [server_row("A")]}, params=params)
    for column in state.tables["servers"].columns:
        assert "script" not in column.link and "sideways" not in column.link


# --------------------------------------------------------------------------- 4. the search


def test_search_matches_a_partial_name_and_a_partial_ucid_with_honest_counts():
    rows = [player_row("Viper-1", "4b1f9c22"), player_row("Maverick", "aa11bb"),
            player_row("Viper-2", "cc33dd")]
    by_name = views.search_players(rows, "viper")
    assert [row.name for row in by_name.rows] == ["Viper-1", "Viper-2"]
    assert (by_name.filtered, by_name.total) == (2, 3), "filtered AND total, never one alone"
    assert by_name.active is True
    assert by_name.label == "Filter: name or UCID contains viper · 2 of 3 players"

    by_ucid = views.search_players(rows, "4B1F")
    assert [row.name for row in by_ucid.rows] == ["Viper-1"], "UCID is case-insensitive too"
    assert (by_ucid.filtered, by_ucid.total) == (1, 3)


def test_an_empty_or_whitespace_query_means_no_filter():
    rows = [player_row("Viper-1", "4b1f9c22"), player_row("Maverick", "aa11bb")]
    for value in (None, "", "   ", "\x00"):
        result = views.search_players(rows, value)
        assert result.active is False, repr(value)
        assert (result.filtered, result.total) == (2, 2)
        assert result.query == ""


def test_the_search_state_and_the_players_table_agree():
    """The table's rows are the FILTERED ones, and the counts on the search record are the
    filtered/total pair — the UI can therefore never print two different answers."""
    params = views.view_params({"q": "viper"})
    rows = {"players": [player_row("Viper-1", "4b1f9c22"), player_row("Maverick", "aa11bb")]}
    state = views.view_state("/", rows, params=params)
    assert len(state.tables["players"].rows) == state.search.filtered == 1
    assert state.search.total == 2, "the total is the unfiltered count"
    assert state.query == "viper"


# ------------------------------------------------- 5. the injection payload, at the data layer


def test_a_script_payload_matches_nothing_and_never_reaches_a_link_unencoded():
    payload = '"><script>alert(1)</script>'
    params = views.view_params({"q": payload})
    rows = {"players": [player_row("Viper-1", "4b1f9c22")]}
    state = views.view_state("/players", rows, params=params)

    assert state.query == payload, "the form may echo it — the ESCAPING is the template's job"
    assert state.search.filtered == 0 and state.search.total == 1
    for column in state.tables["players"].columns:
        assert "<" not in column.link and '"' not in column.link
        assert pairs(column.link)["q"] == payload, "it round-trips through the link, encoded"
