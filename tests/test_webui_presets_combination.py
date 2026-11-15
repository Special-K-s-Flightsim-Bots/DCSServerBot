"""The Presets wizard: combination presets and the resolved preview (S5).

One section per acceptance criterion of the card:

* ``expand_combination`` reads a combination the SAME way ``MizEdit.get_presets`` does — a member the
  file does not carry EXACTLY is dropped, a member whose value is a list is appended as it is;
* ``effective_weather`` folds the members in order, LATER winning per key (and ``wind`` one level
  deeper, as ``MizFile.apply_preset`` unions each altitude);
* the resolved preview, for ONE fixture, EQUALS the reading of ``MizEdit.get_presets`` — the extension's
  own reader — and, when that reader cannot be imported without the bot, the SHARED reader
  ``core.utils.helper.get_preset`` (which agrees with it for a one-level combination); the test that
  calls the extension directly skips when the bot's dependencies are absent;
* the wizard lets the operator switch a preset between its own values and a combination on the SAME
  page, builds and ORDERS a combination, and shows the effective weather;
* reordering the list is visible in the resolved preview — the LAST preset wins where both set a key;
* saving a combination that names a preset the file does not carry, or names none at all, is REFUSED
  with nothing written;
* a legal combination save writes the LIST and leaves the file's other presets intact;
* the GETs write nothing.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused.
"""
from __future__ import annotations

import hashlib
import html as html_module
import logging
import re
import types
from pathlib import Path
from urllib.parse import urlencode

import pytest
from markupsafe import escape

from core.utils import configwrite, helper
from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a preset file carrying two own-value presets a combination names, a plain preset, and the
#: combination itself — one fixture the resolved preview and the extension's reader are compared on.
FIXTURE = """\
Spring:
  date: '2016-04-21'
  temperature: 8
Summer:
  date: '2016-07-01'
  temperature: 30
  clouds:
    preset: Preset1
    density: 7
  wind:
    atGround: {speed: 5, dir: 90}
Morning:
  start_time: morning
  temperature: 20
  clouds:
    density: 4
  wind:
    at2000: {speed: 9, dir: 200}
Winter:
  temperature: -5
Casual Weather:
  - Summer
  - Morning
"""

#: the combination's members, in the order the fixture lists them.
MEMBERS = ["Summer", "Morning"]


#: a combination naming ANOTHER combination — the preview must expand the inner members.
NESTED = """\
Summer:
  temperature: 20
Morning:
  temperature: 30
Inner:
  - Summer
  - Morning
Outer:
  - Inner
  - Missing
"""


def preset_file(config_dir: Path, text: str = FIXTURE, name: str = "presets.yaml") -> Path:
    path = config_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def form_fields(html: str) -> dict[str, str]:
    """The review form's fields: every hidden input, plus the combination editor's visible inputs."""
    out: dict[str, str] = {}
    for match in re.finditer(r'<input type="hidden" name="([^"]*)" value="([^"]*)"', html):
        out[match.group(1)] = html_module.unescape(match.group(2))
    for match in re.finditer(r'<input[^>]*name="(combination|name)"[^>]*value="([^"]*)"', html):
        out.setdefault(match.group(1), html_module.unescape(match.group(2)))
    return out


async def review_form(http, name: str, *, new: bool = False, file: str = "",
                      shape: str = "") -> dict[str, str]:
    """The form the wizard's review step rendered — the fields the save route reads."""
    url = presets_page.PRESETS_NEW_PATH if new else presets_page.wizard_url(name)
    params = {"step": "review"}
    if new:
        params["name"] = name
    if file:
        params["file"] = file
    if shape:
        params["shape"] = shape
    return form_fields(await page(http, f"{url}?{urlencode(params)}"))


# --------------------------------------------------------------- 1. the reading, without a page

def test_parse_and_join_are_the_extensions_own_spelling():
    """A combination is a comma list — the SAME shape the extension splits a preset list on."""
    assert presets_page.parse_combination("Summer, , Morning ") == ["Summer", "Morning"]
    assert presets_page.parse_combination("") == []
    assert presets_page.parse_combination(None) == []
    assert presets_page.combination_text(["Summer", "Morning"]) == "Summer, Morning"
    assert presets_page.preset_shape(["A"]) == presets_page.SHAPE_COMBINATION
    assert presets_page.preset_shape({"date": "x"}) == presets_page.SHAPE_OWN


def test_expand_drops_a_name_the_file_lacks_exactly_and_expands_one_level():
    """A member the file does not carry EXACTLY is dropped; a list member expands ONE level."""
    document = {"A": {"temperature": 1}, "B": ["A"], "C": {"temperature": 2}}
    assert presets_page.expand_combination(document, ["A", "C"]) == [
        ("A", {"temperature": 1}), ("C", {"temperature": 2})]
    # EXACT: a wrongly-cased name is a member that silently does nothing — never resolved.
    assert presets_page.expand_combination(document, ["A", "a"]) == [("A", {"temperature": 1})]
    # a list member is expanded ONE level — the SAME expansion the extension performs
    # (``extension.py:113-121``): each inner member's own value, an inner name it lacks dropped.
    assert presets_page.expand_combination(document, ["B"]) == [("A", {"temperature": 1})]


def test_expand_reads_a_member_that_is_itself_a_combination():
    """A member whose value is a list expands to the INNER members' own values, in order."""
    document = {"Summer": {"temperature": 20}, "Morning": {"temperature": 30},
                "Casual Weather": ["Summer", "Morning"]}
    assert presets_page.expand_combination(document, ["Casual Weather"]) == [
        ("Summer", {"temperature": 20}), ("Morning", {"temperature": 30})]
    # an inner name the document lacks is DROPPED (the extension only logs it), never raised.
    assert presets_page.expand_combination(document, ["Gone"]) == []


def test_effective_weather_folds_later_winning_and_merges_wind():
    """Where two members set the same key the LATER wins; ``wind`` merges one level deeper."""
    members = [("Summer", {"temperature": 30, "wind": {"atGround": {"speed": 5}, "at2000": {"speed": 1}}}),
               ("Morning", {"temperature": 20, "wind": {"at2000": {"speed": 9}}})]
    effective = presets_page.effective_weather(members)
    assert effective["temperature"] == 20                              # the LATER preset wins
    assert effective["wind"]["atGround"] == {"speed": 5}               # merged, not replaced
    assert effective["wind"]["at2000"] == {"speed": 9}                 # the later altitude wins


def test_effective_weather_unions_wind_at_the_same_altitude():
    """Two members setting DIFFERENT sub-keys at the SAME altitude keep BOTH, in either order."""
    summer = {"wind": {"atGround": {"speed": 2}}}
    morning = {"wind": {"atGround": {"dir": 90}, "at2000": {"speed": 5}}}
    forward = presets_page.effective_weather([("Summer", summer), ("Morning", morning)])
    assert forward["wind"]["atGround"] == {"speed": 2, "dir": 90}
    assert forward["wind"]["at2000"] == {"speed": 5}
    backward = presets_page.effective_weather([("Morning", morning), ("Summer", summer)])
    assert backward["wind"]["atGround"] == {"dir": 90, "speed": 2}


def test_the_wind_fold_matches_mizfiles_own_rule():
    """The preview's wind fold is compared against a fold written from ``MizFile``'s own ``|=`` rule."""
    def mizfile_fold(values):
        mission = {"weather": {"wind": {}}}
        for value in values:
            for altitude, row in value.get("wind", {}).items():
                mission["weather"]["wind"].setdefault(altitude, {})
                mission["weather"]["wind"][altitude] |= row
        return mission["weather"]["wind"]

    members = [("Summer", {"wind": {"atGround": {"speed": 2}}}),
               ("Morning", {"wind": {"atGround": {"dir": 90}, "at2000": {"speed": 5}}})]
    assert presets_page.effective_weather(members)["wind"] == mizfile_fold([v for _n, v in members])


# ------------------------------------------------ 2. the preview equals the extension's reading

def test_the_resolved_preview_equals_the_shared_reader(tmp_path):
    """For ONE fixture, the preview's reading EQUALS the shared reader's — the two are compared.

    The reading compared against is ``core.utils.helper.get_preset`` (``core/utils/helper.py:500-529``),
    the SHARED reader the console can import without a bot; it agrees with ``MizEdit.get_presets``
    (``extensions/mizedit/extension.py:105-123``) for a one-level combination (both return the members'
    own values in order, dropping a name the file lacks). The test below calls the extension's reader
    directly when it can be imported.
    """
    path = tmp_path / "presets.yaml"
    path.write_text(FIXTURE, encoding="utf-8")
    document = configwrite.load_config_file(path)

    members = presets_page.expand_combination(document, MEMBERS)
    shared = helper.get_preset(None, "Casual Weather", filename=[str(path)])

    assert [value for _name, value in members] == shared
    # and the resolved preview folds exactly what the shared reading folds — nothing is re-derived.
    assert presets_page.effective_weather(members) == presets_page.effective_weather(
        [("", value) for value in shared])


@pytest.mark.asyncio
async def test_the_same_fixture_against_the_extensions_own_reader(tmp_path):
    """The acceptance's named reading — ``MizEdit.get_presets`` — called directly, when it imports.

    The extension drags the bot's dependencies (Discord, psycopg, ``tomli_w``); where they are absent
    the test SKIPS and the shared-reader comparison above stands, as the card allows.
    """
    try:
        from extensions.mizedit.extension import MizEdit
    except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
        pytest.skip(f"extensions.mizedit cannot be imported without the bot ({ex})")

    path = tmp_path / "presets.yaml"
    path.write_text(FIXTURE, encoding="utf-8")
    document = configwrite.load_config_file(path)

    fake = types.SimpleNamespace(presets=document, config={}, log=logging.getLogger("mizedit-test"))
    got = await MizEdit.get_presets(fake, {"settings": "Casual Weather"})
    members = presets_page.expand_combination(document, MEMBERS)
    assert [value for _name, value in members] == got


# --------------------------------------------------------------- 3. the combination in the wizard

@pytest.mark.asyncio
async def test_a_combination_naming_a_combination_previews_the_inner_weather(config_dir, clean_registry):
    """A member that is ITSELF a combination expands one level, so the preview shows the INNER
    members' weather — and an inner name the file lacks is dropped, not raised."""
    preset_file(config_dir, NESTED)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Outer')}?step=review")
    assert re.search(r"<dt>temperature</dt><dd>30</dd>", html), "the inner members' weather is missing"


@pytest.mark.asyncio
async def test_the_wizard_opens_a_combination_with_its_names_and_candidates(config_dir, clean_registry):
    """Opening a combination renders its ordered names and the file's OTHER presets to add."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Casual Weather')}?step=review")

    assert 'name="combination" value="Summer, Morning"' in html
    # the ordered members, in order, in the list
    rows = re.findall(r'<span class="nm">([^<]*)</span>', html)
    assert rows[:2] == ["Summer", "Morning"]
    # the candidates are the file's OTHER presets — a member already named is not offered again
    assert re.search(r'>Spring</a>', html) and re.search(r'>Winter</a>', html)
    assert not re.search(r'>Summer</a>', html) and not re.search(r'>Morning</a>', html)


@pytest.mark.asyncio
async def test_the_shape_switch_is_on_the_wizard(config_dir, clean_registry):
    """One preset, two shapes: the switch to its own values OR a combination is on the SAME page."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=time")

    assert presets_page.WIZARD_SHAPE_HEAD in html
    assert f'{presets_page.SHAPE_FIELD}={presets_page.SHAPE_OWN}' in html
    assert f'{presets_page.SHAPE_FIELD}={presets_page.SHAPE_COMBINATION}' in html
    # the own shape renders the weather rows; the combination shape renders the editor instead
    assert 'class="pw-f"' in html
    assert 'name="combination"' not in html


@pytest.mark.asyncio
async def test_switching_a_preset_to_a_combination_swaps_the_panel(config_dir, clean_registry):
    """``?shape=combination`` on an own-value preset swaps the weather rows for the combination editor."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=time"
                                 f"&{presets_page.SHAPE_FIELD}={presets_page.SHAPE_COMBINATION}")

    assert 'name="combination"' in html and presets_page.WIZARD_COMBO_HEAD in html
    assert 'class="pw-f"' not in html                       # the weather rows are gone for a combination


@pytest.mark.asyncio
@pytest.mark.parametrize("step", presets_page.WIZARD_STEP_VALUES)
async def test_every_step_renders_the_combination_editor(config_dir, clean_registry, step):
    """A combination renders the editor on EVERY step, with the ordered names (the rail still works)."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Casual Weather')}?step={step}")
    assert 'name="combination" value="Summer, Morning"' in html
    assert re.findall(r'<span class="nm">([^<]*)</span>', html)[:2] == ["Summer", "Morning"]


@pytest.mark.asyncio
async def test_reordering_the_list_is_visible_in_the_resolved_preview(config_dir, clean_registry):
    """The LAST preset wins where both set a key: reorder the names and the effective weather changes."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        first = await page(http, f"{presets_page.wizard_url('Casual Weather')}?step=review"
                                 "&combination=Summer, Morning")
        second = await page(http, f"{presets_page.wizard_url('Casual Weather')}?step=review"
                                  "&combination=Morning, Summer")

    assert re.search(r"<dt>temperature</dt><dd>20</dd>", first)   # Morning (later) wins
    assert re.search(r"<dt>temperature</dt><dd>30</dd>", second)  # Summer (later) wins
    # the ordered list itself follows the reorder
    assert re.findall(r'<span class="nm">([^<]*)</span>', first)[:2] == ["Summer", "Morning"]
    assert re.findall(r'<span class="nm">([^<]*)</span>', second)[:2] == ["Morning", "Summer"]


# ------------------------------------------------------------------- 4. the dangling-name guard

@pytest.mark.asyncio
async def test_saving_a_combination_with_a_dangling_name_is_refused(config_dir, clean_registry):
    """A member the file does not carry is refused and NOTHING is written — never a silent no-op."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Casual Weather")
        data[presets_page.SHAPE_FIELD] = presets_page.SHAPE_COMBINATION
        data[presets_page.COMBINATION_FIELD] = "Summer, Nope"
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text
        assert sha256(path) == before, "a dangling name must write nothing"
        html = await page(http, reply.headers["location"])

    assert "Nope" in html and "does not carry" in html


@pytest.mark.asyncio
async def test_a_wrongly_cased_member_is_refused(config_dir, clean_registry):
    """The match is EXACT: a member in the wrong case is the same silent no-op, so it is refused too."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Casual Weather")
        data[presets_page.SHAPE_FIELD] = presets_page.SHAPE_COMBINATION
        data[presets_page.COMBINATION_FIELD] = "Summer, morning"
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, reply.headers["location"])
    # the refusal names the wrongly-cased member (autoescaped, hence the quotes as entities)
    assert str(escape("'morning'")) in html and "does not carry" in html


@pytest.mark.asyncio
async def test_saving_an_empty_combination_is_refused(config_dir, clean_registry):
    """A combination that names nothing is the same defect: refused, nothing written."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Casual Weather")
        data[presets_page.SHAPE_FIELD] = presets_page.SHAPE_COMBINATION
        data[presets_page.COMBINATION_FIELD] = "   "
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, reply.headers["location"])
    assert "names no presets" in html


# ------------------------------------------------------------------- 5. a legal combination save

@pytest.mark.asyncio
async def test_a_legal_combination_save_writes_the_list(config_dir, clean_registry):
    """The positive control: a combination naming existing presets writes the LIST, siblings intact."""
    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Casual Weather")
        data[presets_page.SHAPE_FIELD] = presets_page.SHAPE_COMBINATION
        data[presets_page.COMBINATION_FIELD] = "Morning, Summer, Winter"
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    document = configwrite.load_config_file(path)
    assert document["Casual Weather"] == ["Morning", "Summer", "Winter"]
    assert document["Summer"]["temperature"] == 30       # the members are untouched
    assert document["Spring"]["temperature"] == 8


@pytest.mark.asyncio
async def test_a_new_combination_is_built_and_saved(config_dir, clean_registry):
    """A NEW preset can be built as a combination: the editor carries the name, the save writes the list."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.PRESETS_NEW_PATH}?step=review&name=Fresh"
                                 f"&{presets_page.SHAPE_FIELD}={presets_page.SHAPE_COMBINATION}"
                                 f"&{presets_page.COMBINATION_FIELD}=Summer")
        data = form_fields(html)
        assert data[presets_page.COMBINATION_FIELD] == "Summer"
        assert data["name"] == "Fresh"
        data[presets_page.SHAPE_FIELD] = presets_page.SHAPE_COMBINATION
        data[presets_page.COMBINATION_FIELD] = "Summer, Winter"
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    document = configwrite.load_config_file(path)
    assert document["Fresh"] == ["Summer", "Winter"]
    assert "Casual Weather" in document


@pytest.mark.asyncio
async def test_the_review_dumps_the_combination_as_a_list(config_dir, clean_registry):
    """The produced YAML is the document dumped by the ONE writer, and it carries the LIST."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Casual Weather')}?step=review")
    assert re.search(r"Casual Weather:\s*\n\s*- Summer", html), "the produced YAML is not a list"


# --------------------------------------------------------------- 6. the GETs write nothing

@pytest.mark.asyncio
async def test_the_combination_reads_write_nothing(config_dir, clean_registry):
    """Every GET leaves the preset file byte-identical — there is no write in the read path."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await page(http, f"{presets_page.wizard_url('Casual Weather')}?step=review")
        await page(http, presets_page.PRESETS_NEW_PATH)
        await page(http, f"{presets_page.wizard_url('Spring')}?step=time"
                         f"&{presets_page.SHAPE_FIELD}={presets_page.SHAPE_COMBINATION}")
    assert sha256(path) == before
    assert not list(config_dir.glob("*.bak"))
