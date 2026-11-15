"""The reveal toggle's contract, the resume-mode option words, and the Mission block leaving the tab.

The reveal contract is driven through the shipped script (``reveal_harness.js``): with a password
stored the field holds the real value, so the eye is live and toggles the masking; an empty field is
not revealable -- the button is disabled and names the reason and a press leaves the field's ``type``
alone; the button goes inert again when the field is emptied; a press never submits, clears or rewrites
the value. ``advanced.resume_mode`` carries meaning in the option TEXT while the option VALUE stays the
number."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import run_node_harness
from test_webui_d3_config_ux import _config_page  # noqa: F401 - the page fixture, one owner
from test_webui_server_config import (  # noqa: F401 - the fixtures are imported on purpose
    BASE_SETTINGS, _browser_select_value, config_dir,
)

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services" / "webservice"
REVEAL_JS = SERVICES / "static" / "reveal.js"
HARNESS = TESTS / "reveal_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


# ------------------------------------------------------------------ 1. the reveal toggle, by pressing it

@pytest.fixture(scope="module")
def reveal() -> dict:
    """The shipped script's own answers, in the eight states the harness boots it in."""
    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert REVEAL_JS.is_file(), "the shipped reveal script is missing"
    done = run_node_harness(["node", str(HARNESS), str(REVEAL_JS)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@requires_node
def test_a_stored_password_is_revealable_and_the_press_shows_it(reveal):
    """The state the operator meets on arrival: the field holds the STORED password, so the eye is live."""
    stored = reveal["stored"]
    assert stored["before"]["fieldValue"] == "S3cretPass", \
        "the field must ship holding the stored value for this to be the state that was reported"
    assert stored["before"]["hidden"] is False, "a live form's eye is offered, not hidden"
    assert stored["before"]["disabled"] is False, "with text in the field the press must be offered"
    assert stored["before"]["label"] == "Show password", "and it says what the next press does"
    assert stored["revealed"]["fieldType"] == "text", "the press reveals what the field holds"
    assert stored["revealed"]["fieldValue"] == "S3cretPass", "the field's own value, unchanged"
    assert stored["revealed"]["pressed"] == "true", "the announced state flips with it"
    assert stored["revealed"]["label"] == "Hide password", \
        "and the name follows the state, so the next press is not a lie"
    assert stored["hiddenAgain"]["fieldType"] == "password", "the second press masks it again"
    assert stored["hiddenAgain"]["pressed"] == "false"
    assert stored["hiddenAgain"]["label"] == "Show password"


@requires_node
def test_an_empty_field_has_nothing_to_reveal(reveal):
    empty = reveal["empty"]
    assert empty["before"]["fieldValue"] == ""
    assert empty["before"]["disabled"] is True, "an empty field is not revealable text"
    assert empty["afterClick"]["fieldType"] == "password", "a press here must change nothing"
    assert "type a password" in empty["before"]["label"]


@requires_node
def test_the_button_works_the_moment_there_is_something_to_show(reveal):
    """The eye's range: text the reader typed into an empty field behaves as the stored value does."""
    typed = reveal["typed"]
    assert typed["armed"]["disabled"] is False, "the first keystroke makes the eye live"
    assert typed["armed"]["label"] == "Show password", "and it says what the next press does"
    assert typed["revealed"]["fieldType"] == "text", "the press reveals what was typed"
    assert typed["revealed"]["fieldValue"] == "N3wPass", "the field's own value, unchanged"
    assert typed["revealed"]["pressed"] == "true"
    assert typed["revealed"]["label"] == "Hide password"
    assert typed["hiddenAgain"]["fieldType"] == "password", "the second press masks it again"
    assert typed["hiddenAgain"]["pressed"] == "false"
    assert typed["hiddenAgain"]["label"] == "Show password"


@requires_node
def test_an_emptied_field_is_inert_again_and_the_disabled_form_is_left_alone(reveal):
    emptied = reveal["emptiedAgain"]
    assert emptied["fieldType"] == "password", "the field must not stay revealed when it is emptied"
    assert emptied["disabled"] is True, "and there is nothing left to show"
    disabled = reveal["disabledForm"]
    assert disabled["hidden"] is True, \
        "a form the server cannot take a change from keeps the eye out of the way"


@requires_node
def test_the_press_never_writes_the_value_and_never_submits(reveal):
    assert reveal["valueUntouched"] is True, "revealing reveals: the value is never rewritten"


# ------------------------------------------------------------------ 2. the resume-mode words

async def _select_html(config_dir, value) -> tuple[str, str]:
    settings = dict(BASE_SETTINGS, advanced={"resume_mode": value, "maxPing": 300})
    html = await _config_page(config_dir, settings=settings)
    match = re.search(r'<select name="advanced\.resume_mode"[^>]*>(.*?)</select>', html, re.S)
    assert match, "the resume-mode select must be rendered"
    return match.group(0), html


@pytest.mark.asyncio
async def test_every_resume_mode_option_says_what_its_number_means(config_dir):
    """Each option's text states its meaning: 0 = always run, 1 = always pause, 2 = pause without clients."""
    select, _html = await _select_html(config_dir, 1)
    for number, words in ((0, "always run"), (1, "always pause"), (2, "pause without clients")):
        assert re.search(rf'<option value="{number}"[^>]*>\s*{number} — {re.escape(words)}\s*</option>',
                         select), f"option {number} must carry its meaning, not its number"
    assert ">0</option>" not in select and ">1</option>" not in select and ">2</option>" not in select, \
        "no option may render its bare number as its text"


@pytest.mark.asyncio
async def test_the_options_still_submit_the_numbers(config_dir):
    """The words are presentation only: the value a browser sends is still the number."""
    select, html = await _select_html(config_dir, 1)
    assert _browser_select_value(html, "advanced.resume_mode") == "1", \
        "the selected option's submitted value is still the number"
    assert select.count('value="0"') == 1 and select.count('value="1"') == 1 \
        and select.count('value="2"') == 1, "each option keeps its numeric value"
    assert '<option value=""' in select, "the leading unchanged option is still there"


@pytest.mark.asyncio
async def test_a_value_that_is_not_offered_still_defaults_to_unchanged(config_dir):
    """An unlisted current value must still leave the browser on the empty choice, so a change is explicit."""
    _select, html = await _select_html(config_dir, 7)
    assert _browser_select_value(html, "advanced.resume_mode") == ""


# ------------------------------------------------------------------ 3. the Mission block leaves the tab

@pytest.mark.asyncio
async def test_the_configuration_tab_holds_settings_and_no_mission_block(config_dir):
    _select, html = await _select_html(config_dir, 1)
    assert "Mission list — owned by a feature of its own" not in html, "the block must be gone"
    for key in ("missionList", "listStartIndex"):
        assert key not in html, f"{key} is the missions feature's, not a configuration setting"
    assert 'name="current"' not in html
 # the settings that ARE this tab's are untouched by the removal
    assert 'name="description"' in html and 'name="port"' in html and 'name="maxPlayers"' in html
    assert "Discord channels" in html, "the servers.yaml face stays where it was"
