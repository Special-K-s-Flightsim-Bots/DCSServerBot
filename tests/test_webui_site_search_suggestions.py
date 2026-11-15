"""The public site's search suggestions: the JSON sibling of the pilot search page.

WHAT THIS SUITE PINS
--------------------
* ``<site>/search.json?q=`` answers the SAME read the search page uses (``site_reads.pilot_search``),
  once, with ``{"pilots": [{"name", "href"}]}`` rows that link to a pilot's profile;
* an EMPTY or ONE-character query answers an empty list and runs NO statement at all;
* the answer is scoped to its own site, exactly like the page: another site's pilots are absent;
* the route is registered ``public`` on every site and answers a signed-out GET with 200;
* the suggestions are most recently seen first and capped at :data:`SUGGESTION_LIMIT`;
* the shell loads the shipped script with an asset version and carries the form's ``data-search``;
* the SHIPPED ``site-search.js``, driven by ``site_search_harness.js``: a typed burst asks ONCE, a
  stale answer is ignored, the same fragment is not asked twice, a pick navigates to the row's
  href, Escape closes, and Enter with a highlighted row does not submit the form.

A real browser and a real installation were NOT exercised: the script is run against a DOM stub and
the pages against the suite's stub pool.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import run_node_harness
from services.webservice import permissions, site_pages
from services.webservice.pages import site_players
from test_webui_site_pages import (  # noqa: F401 - the site suite's harness, reused on purpose
    MANY, PILOTS, build, bus, config_dir, get,
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "services" / "webservice" / "static" / "site-search.js"
HARNESS = Path(__file__).resolve().parent / "site_search_harness.js"
TEMPLATE = REPO / "services" / "webservice" / "templates" / "site_base.html"


# --------------------------------------------------------------------------- the JSON route


def test_the_suggestion_route_answers_the_reads_rows_with_name_and_href(config_dir, bus):
    """A two-character fragment reaches the page's own read once and answers name+href rows."""
    app, _ = build(config_dir)
    response = get(app, "/search.json?q=root")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert set(payload) == {"pilots"}
    names = [row["name"] for row in payload["pilots"]]
    assert set(names) == {"Root One", "Root One Alt", "Root Two"}, \
        "the route did not answer the read's rows"
    by_name = {row["name"]: row["href"] for row in payload["pilots"]}
    assert by_name["Root One"] == "/player/u-root-1"
    assert "Group One" not in names, "another site's pilot reached the answer"
    reads = [statement for statement, _params in bus.apool.calls if "LOWER(p.name) LIKE" in statement]
    assert len(reads) == 1, "the suggestion route did not run the read exactly once"


def test_an_empty_or_one_character_query_answers_nothing_and_runs_nothing(config_dir, bus):
    """A lone letter is not a search: the answer is an empty list and no statement runs."""
    app, _ = build(config_dir)
    for path in ("/search.json", "/search.json?q=", "/search.json?q=r"):
        response = get(app, path)
        assert response.status_code == 200
        assert response.json() == {"pilots": []}, f"{path}: a suggestion was offered"
    statements = [statement for statement, _params in bus.apool.calls]
    assert not any("LOWER(p.name) LIKE" in statement for statement in statements), \
        "the read ran for an empty or one-character query"


def test_the_suggestion_route_is_scoped_to_its_own_site(config_dir, bus):
    """Each site answers its own pilots only, through the same scoped read as its search page."""
    app, _ = build(config_dir)
    group1 = get(app, "/group1/search.json?q=one")
    assert group1.status_code == 200
    names = [row["name"] for row in group1.json()["pilots"]]
    assert names == ["Group One"], f"/group1 answered {names!r}"
    assert group1.json()["pilots"][0]["href"] == "/group1/player/u-group-1"


def test_the_suggestion_route_is_public_on_every_site(config_dir, bus):
    """Registered ``public`` like the search page, and a signed-out GET answers 200."""
    app, registrar = build(config_dir)
    assert registrar.capability_for(path="/search.json") == permissions.PUBLIC
    assert registrar.capability_for(path="/group1/search.json") == permissions.PUBLIC
    assert get(app, "/search.json?q=root").status_code == 200
    assert get(app, "/group1/search.json?q=one").status_code == 200


def test_suggestions_are_most_recently_seen_first_and_capped():
    """The page's read orders by name; the popup re-orders by recency and caps the rows."""
    rows = [{"name": f"Pilot {index:02d}", "ucid": f"u-{index:02d}",
             "last_seen": f"2026-01-{index:02d}T00:00:00+00:00"} for index in range(1, 31)]
    resolved = type("Resolved", (), {"prefix": "/group1"})()
    answer = {"rows": list(rows)}
    found = site_players.suggestions(resolved, answer)
    assert len(found) == site_players.SUGGESTION_LIMIT == 25
    assert found[0] == {"name": "Pilot 30", "href": "/group1/player/u-30"}, \
        "the suggestions are not ordered by when the pilot was last seen"
    assert found[-1]["name"] == "Pilot 06", "the cap did not keep the most recent 25"


def test_the_shell_loads_the_script_and_carries_the_routes(config_dir, bus):
    """The form carries the suggestion route and the page loads the shipped file, versioned."""
    app, _ = build(config_dir)
    markup = get(app, "/group1/players?q=one").text
    assert 'data-search="/group1/search.json"' in markup, \
        "the search form does not carry the suggestion route"
    assert re.search(r'src="/static/site-search\.js\?v=\d+" defer', markup), \
        "the page does not load the shipped script with an asset version"


# --------------------------------------------------------------------------- the shipped script

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def states() -> dict:
    """The shipped script's own answers, booted against a minimal DOM by the harness."""
    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert SCRIPT.is_file(), "the shipped search script is missing"
    done = run_node_harness(["node", str(HARNESS), str(SCRIPT)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["states"]


@requires_node
def test_a_typed_burst_asks_once_and_the_popup_is_the_combobox(states):
    """One request per burst, nothing for a lone character, and the popup's own contract."""
    assert states["initial"]["fetches"] == [], "the script fetched before anything was typed"
    assert states["one_char"]["fetches"] == [], "one character went out as a request"
    assert states["typed"]["fetches"] == [], "a request went out before the burst ended"
    assert states["asked"]["fetches"] == ["/group1/search.json?q=vi"], \
        "one typed burst did not resolve to exactly one request"
    offered = states["offered"]
    assert offered["list"]["hidden"] is False
    assert offered["list"]["role"] == "listbox"
    assert offered["list"]["label"] == "Find a pilot", \
        "the popup is not named by the field's own label"
    assert offered["list"]["expanded"] == "true"
    assert offered["list"]["controls"] == offered["list"]["id"]
    assert offered["combobox"] == {"role": "combobox", "autocomplete": "list"}
    assert offered["wrapped"] is True
    assert [row["name"] for row in offered["rows"]] == ["Viper", "Viper Two"]
    assert [row["role"] for row in offered["rows"]] == ["option", "option"]
    assert [row["selected"] for row in offered["rows"]] == ["true", "false"], \
        "the first suggestion is not the one Enter would take"


@requires_node
def test_a_pick_navigates_to_the_rows_href_and_the_keys_move(states):
    """A press takes the row under the pointer; Down/Up move through the list."""
    assert states["press_event"]["prevented"] is True, \
        "the press was not held, so the box would lose focus before the pick"
    picked = states["picked"]
    assert picked["visited"] == ["/player/u-1"], "a pick did not navigate to the row's href"
    assert picked["list"]["hidden"] is True, "the popup stayed open after a pick"
    assert [row["selected"] for row in states["after_down"]["rows"]] == ["false", "true"]
    assert [row["selected"] for row in states["after_up"]["rows"]] == ["true", "false"]


@requires_node
def test_enter_with_a_highlighted_row_does_not_submit_the_form(states):
    """Enter takes the highlighted row and is swallowed; with nothing offered it is the submit."""
    assert states["enter"]["prevented"] is True, \
        "Enter was not swallowed with a suggestion highlighted: the form would submit"
    assert states["enter_picked"]["visited"] == states["after_up"]["visited"] + ["/player/u-1"], \
        "Enter did not take the highlighted suggestion"
    assert states["enter_alone"]["prevented"] is False, \
        "Enter was swallowed with no suggestion offered: the form could not be submitted"


@requires_node
def test_escape_closes_without_changing_what_was_typed(states):
    """Escape puts the list away and leaves the box's text exactly as it was."""
    assert states["escape"]["prevented"] is True
    after = states["after_escape"]
    assert after["list"]["hidden"] is True and after["rows"] == []
    assert after["value"] == "vipe", "Escape changed what the visitor typed"


@requires_node
def test_an_empty_or_refused_answer_leaves_the_plain_form(states):
    """Nobody matched, or the route refused: no popup, no error, the form as it was."""
    for state in ("empty", "refused"):
        answer = states[state]
        assert answer["rows"] == [], f"{state}: a popup was shown"
        assert answer["list"]["hidden"] is True, f"{state}: the popup stayed open"
        assert answer["list"]["expanded"] == "false"


@requires_node
def test_a_stale_answer_is_ignored_and_the_same_fragment_is_not_asked_twice(states):
    """The answer to a fragment the box has moved on from never paints; a repeat is not re-asked."""
    assert states["stale_ignored"]["rows"] == [], \
        "the answer to the abandoned fragment was painted"
    assert states["stale_ignored"]["list"]["hidden"] is True
    taken = states["answer_taken"]
    assert [row["name"] for row in taken["rows"]] == ["Answer To viper", "Another"], \
        "the answer that belongs to the box's current fragment was lost"
    same = states["same_fragment"]
    assert same["fetches"].count("/group1/search.json?q=viper") == 1, \
        "the same fragment was asked for twice"
