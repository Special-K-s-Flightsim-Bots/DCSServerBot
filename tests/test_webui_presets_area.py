"""The Presets area: the read — one card per preset, its kind, and the keys it writes.

One section per acceptance criterion of the card:

* the discovery is the bot's own (``config/presets/*.yaml``, then ``config/presets*.yaml``), read
  through the ONE config primitive — the pure read is exercised directly, then through the page;
* the page carries ONE card per TOP-LEVEL KEY of every discovered preset file, and each card's key
  chips are that preset's OWN top-level keys (operator data, never a label);
* a LIST-valued preset is badged as a combination and shows its members;
* a preset carrying ``modify`` is LISTED, badged as the expert DSL, and offers NO wizard link, while
  every other preset links into the wizard at the URL the page module declares;
* the picture band is a PLACEHOLDER — no sky is drawn;
* the toolbar's filter is a REAL GET (``?kind=``, and the search ``?q=``), server-side;
* the two honest empty states — no preset file at all, and a file that does not parse — each state
  PLAINLY which one it is, with the other files' presets still rendered;
* the page is REFUSED for a caller without ``presets.view``;
* the GET writes NOTHING: every preset file's bytes are sha256-identical afterwards, and no ``.bak``
  or new file appeared.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from services.webservice.pages import presets as presets_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    PASSWORD, build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a preset file carrying every kind the area knows, plus the keys each preset writes.
PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  clouds:
    preset: Preset1
Morning:
  start_time: 08:00
Casual Weather:
  - Summer
  - Morning
Carrier Freqs:
  modify:
    file: mission
    variables:
      theatre: theatre
"""

#: a preset name, its kind, and the chips its card must carry.
CARDS = (
    ("Spring", presets_page.KIND_OWN, ("date", "temperature", "clouds")),
    ("Morning", presets_page.KIND_OWN, ("start_time",)),
    ("Casual Weather", presets_page.KIND_COMBINATION, ("Summer", "Morning")),
    ("Carrier Freqs", presets_page.KIND_MODIFY, ("modify",)),
)

#: the console's ONE tile component draws a preset card; the page must not carry a second lookalike.
_CARD_SPLIT = re.compile(r'<article class="st-tile preset">')


def preset_file(config_dir: Path, text: str = PRESETS_YAML, name: str = "presets.yaml") -> Path:
    """Write a preset file into the install's config directory and return its path."""
    path = config_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def cards_of(html: str) -> list[str]:
    """The page's card blocks, in render order — the unit the acceptance criteria are read over."""
    return [chunk for chunk in _CARD_SPLIT.split(html)[1:]]


def a_card(html: str, name: str) -> str:
    """The ONE card whose name is *name*."""
    matches = [chunk for chunk in cards_of(html) if f'<h3 class="st-name">{name}</h3>' in chunk]
    assert len(matches) == 1, f"expected exactly one card for '{name}', got {len(matches)}"
    return matches[0]


async def presets_html(http, path: str = presets_page.PRESETS_PATH) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def digests(config_dir: Path) -> dict[str, str]:
    """Every preset file's sha256 under the config tree, plus every name in it."""
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(config_dir.rglob("presets*.yaml"))}


# --------------------------------------------------------------- 1. the read, without a page

def test_the_discovery_is_the_bots_own_directory_then_the_flat_files(tmp_path):
    """``config/presets/*.yaml`` first, then ``config/presets*.yaml``, each in file-name order, once."""
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "b.yaml").write_text("B: {}\n", encoding="utf-8")
    (tmp_path / "presets" / "a.yaml").write_text("A: {}\n", encoding="utf-8")
    for name in ("presets.yaml", "presets_local.yaml"):
        (tmp_path / name).write_text("X: {}\n", encoding="utf-8")
    found = presets_page.discover_preset_files(tmp_path)
    assert [path.name for path in found] == ["a.yaml", "b.yaml", "presets.yaml", "presets_local.yaml"]


def test_every_preset_file_is_read_through_the_one_config_primitive(tmp_path, monkeypatch):
    """The page's read goes through ``configwrite.load_config_file`` — never a second YAML reading."""
    preset_file(tmp_path)
    seen: list[str] = []
    original = presets_page.configwrite.load_config_file

    def spy(path):
        seen.append(str(path))
        return original(path)

    monkeypatch.setattr(presets_page.configwrite, "load_config_file", spy)
    cards, unreadable, found = presets_page.read_presets(tmp_path)
    assert seen == [str(tmp_path / "presets.yaml")]
    assert found is True and unreadable == []
    assert [card["name"] for card in cards] == [name for name, _kind, _keys in CARDS]


def test_the_kind_is_the_values_own_shape_and_the_keys_are_the_presets_own(tmp_path):
    """A list is a combination, a map carrying ``modify`` is the expert DSL, the rest are own values."""
    assert presets_page.preset_kind(["Summer"]) == presets_page.KIND_COMBINATION
    assert presets_page.preset_kind({"modify": "x", "date": "y"}) == presets_page.KIND_MODIFY
    assert presets_page.preset_kind({"date": "y"}) == presets_page.KIND_OWN
    assert presets_page.preset_kind(None) == presets_page.KIND_OWN
    # the chips are the preset's OWN keys — a map's top-level keys, a combination's member names
    assert presets_page.preset_keys({"date": "y", "clouds": {"preset": "Preset1"}}) == ["date", "clouds"]
    assert presets_page.preset_keys(["Summer", "Morning"]) == ["Summer", "Morning"]
    assert presets_page.preset_keys(None) == []


# --------------------------------------------------------------- 2. one card per preset, its kind

@pytest.mark.asyncio
async def test_one_card_per_top_level_key_with_its_own_keys_as_chips(config_dir, clean_registry):
    """N top-level keys render N cards, each carrying the preset's own key names as DATA."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")                          # Admin
        html = await presets_html(http)

    assert len(cards_of(html)) == len(CARDS), "one card per top-level key, no more and no less"
    for name, kind, keys in CARDS:
        card = a_card(html, name)
        assert (f'<span class="st-scope">{presets_page.KIND_LABELS[kind]}</span>' in card), \
            f"'{name}' rendered the wrong kind badge"
        chips = re.findall(r"<span>([^<]*)</span>", card.split('class="ps-sets"')[1].split("</div>")[0])
        assert chips == list(keys), f"'{name}' chips are not its own top-level keys: {chips}"
    assert "config/presets.yaml" in cards_of(html)[0], "a card does not name the file it came from"


@pytest.mark.asyncio
async def test_a_list_valued_preset_is_badged_as_a_combination(config_dir, clean_registry):
    """The combination is its own badge and shows the names it merges — never a weather card."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await presets_html(http)
    card = a_card(html, "Casual Weather")
    assert presets_page.KIND_LABELS[presets_page.KIND_COMBINATION] in card
    assert presets_page.KIND_LABELS[presets_page.KIND_OWN] not in card


@pytest.mark.asyncio
async def test_a_modify_preset_is_listed_badged_and_offers_no_wizard_link(config_dir, clean_registry):
    """The expert DSL is LISTED and never opened by the wizard; every other preset offers a link."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await presets_html(http)

    expert = a_card(html, "Carrier Freqs")
    assert presets_page.KIND_LABELS[presets_page.KIND_MODIFY] in expert
    assert "/wizard" not in expert, "the modify preset offered a wizard link"
    assert presets_page.PRESETS_MODIFY_NOTE in expert, "the modify card carries no note about its editor"

    for name, kind, _keys in CARDS:
        if kind in presets_page.WIZARD_KINDS:
            assert presets_page.wizard_url(name) in a_card(html, name), \
                f"'{name}' offers no wizard link"
    assert presets_page.wizard_url("Casual Weather") in a_card(html, "Casual Weather"), \
        "a combination preset must be openable by the wizard"


@pytest.mark.asyncio
async def test_no_card_draws_a_sky(config_dir, clean_registry):
    """The card is the console's tile: a kind glyph, no picture band and no drawn sky."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await presets_html(http)
    for card in cards_of(html):
        assert "ps-prev" not in card and "<img" not in card, \
            "a card drew a picture band or an image"
        assert "M7.2 3.2h6.1l3.5 3.5v9.6" in card, "the card carries no kind glyph"


# --------------------------------------------------------------- 3. the toolbar's real GET

@pytest.mark.asyncio
async def test_the_kind_filter_is_a_server_side_get(config_dir, clean_registry):
    """``?kind=`` filters on the SERVER: the combination view shows the combination and nothing else."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        combined = await presets_html(http, f"{presets_page.PRESETS_PATH}?kind={presets_page.KIND_COMBINATION}")
        unknown = await presets_html(http, f"{presets_page.PRESETS_PATH}?kind=nonsense")
        searched = await presets_html(http, f"{presets_page.PRESETS_PATH}?q=clouds")

    assert len(cards_of(combined)) == 1 and a_card(combined, "Casual Weather")
    assert len(cards_of(unknown)) == len(CARDS), "an unknown kind must fall back to every preset"
    # the search is on the SERVER too: only the preset that WRITES `clouds` answers
    assert [*_names(searched)] == ["Spring"]


def _names(html: str) -> list[str]:
    return re.findall(r'<h3 class="st-name">([^<]*)</h3>', html)


# --------------------------------------------------------------- 4. the two honest empty states

@pytest.mark.asyncio
async def test_no_preset_file_at_all_says_so(config_dir, clean_registry):
    """Nothing is a state, not an empty grid: the page states which one it is."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await presets_html(http)
    assert presets_page.PRESETS_NO_FILE in html
    assert cards_of(html) == [], "the page rendered a card grid with no preset file"


@pytest.mark.asyncio
async def test_a_file_that_does_not_parse_is_named_and_the_others_still_render(config_dir, clean_registry):
    """A broken file gets its OWN sentence; the readable file's presets still render. Never a 500."""
    preset_file(config_dir, "Spring:\n  date: [unclosed\n", name="presets.yaml")
    preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await presets_html(http, f"{presets_page.PRESETS_PATH}?file=")
    assert "config/presets.yaml could not be read as YAML" in html
    assert [*_names(html)] == ["Summer"], "the unreadable file took the readable one's presets down"


# --------------------------------------------------------------- 5. the door, and the no-write

@pytest.mark.asyncio
async def test_the_page_is_refused_without_the_capability(config_dir, clean_registry):
    """A caller who does not hold ``presets.view`` is refused; the console's own rule does it."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")                             # DCS: no capability at all
        assert (await http.get(presets_page.PRESETS_PATH)).status_code == 403
    async with client(app) as http:
        assert (await http.get(presets_page.PRESETS_PATH)).status_code == 403   # anonymous


@pytest.mark.asyncio
async def test_the_read_writes_nothing(config_dir, clean_registry):
    """The GET leaves the install byte-identical: every preset file, sha256, before and after."""
    preset_file(config_dir)
    preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    before = digests(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await presets_html(http)
        await presets_html(http, f"{presets_page.PRESETS_PATH}?kind={presets_page.KIND_OWN}&q=date")
    assert digests(config_dir) == before, "the read changed a preset file"
    assert not list(config_dir.glob("*.bak")), "the read left a backup behind"
