"""The Presets area remembers each parsed preset file — and never serves a stale one.

One section per acceptance criterion of the card:

* a second render of unchanged files parses NOTHING (one parse per file, then zero);
* a file written OUTSIDE the page — ``presets.yaml`` written directly, the way the Discord
  ``/mission save preset`` command writes it — is reflected on the very NEXT render, with no console
  save in between;
* a file whose mtime moved but whose content did not, and a file whose size changed, both re-parse;
* a deleted file is not resurrected from what was remembered, and a file that reappears is read again;
* a file that does not parse is remembered as its REFUSAL, and its sentence still renders;
* both bounds hold: more files than the file cap evicts the oldest, and a document over the byte
  budget is not held;
* both save exits forget what they made stale, and the cache is not the only staleness story.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source) wherever a page renders, with the write suite's own harness reused.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from services.webservice.pages import presets as presets_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, sign_in,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SPRING = """\
Spring:
  date: '2016-04-21'
  temperature: 8
"""

SUMMER = """\
Summer:
  temperature: 23
"""


@pytest.fixture(autouse=True)
def empty_cache():
    """The cache is PROCESS state: every test starts with nothing remembered."""
    presets_page.forget_preset_documents()
    yield
    presets_page.forget_preset_documents()


@pytest.fixture
def parses(monkeypatch):
    """The preset files actually parsed, in order — a spy on the ONE config reader."""
    seen: list[str] = []
    original = presets_page.configwrite.load_config_file

    def spy(path):
        seen.append(str(path))
        return original(path)

    monkeypatch.setattr(presets_page.configwrite, "load_config_file", spy)
    return seen


def preset_file(root: Path, text: str = SPRING, name: str = "presets.yaml") -> Path:
    """Write a preset file into the install's config directory and return its path."""
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


async def presets_html(http, path: str = presets_page.PRESETS_PATH) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def card_names(html: str) -> list[str]:
    """Every tile's name, in render order (the console's one tile component draws it)."""
    return re.findall(r'<h3 class="st-name">([^<]*)</h3>', html)


def preset_parses(parses: list[str]) -> list[str]:
    """The preset files among *parses* — the config documents beside them are a separate read."""
    return [path for path in parses if "scheduler.yaml" not in path and "nodes.yaml" not in path]


def write_like_the_mission_command(path: Path, text: str) -> None:
    """Write *path* exactly as ``/mission save preset`` does: a plain ``open(..., 'w')``."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# --------------------------------------------------------------- 1. one parse per file, not per render

def test_a_second_render_parses_nothing(tmp_path, parses):
    """One parse per file on the first read, ZERO on the second — the whole point of the card."""
    preset_file(tmp_path, name="presets.yaml")
    preset_file(tmp_path, SUMMER, name="presets2.yaml")
    first, _unreadable, _found = presets_page.read_presets(tmp_path)
    assert len(parses) == 2, f"the first read parsed {len(parses)} files, not one per file"
    assert [card["name"] for card in first] == ["Spring", "Summer"]
    parses.clear()
    second, _unreadable, _found = presets_page.read_presets(tmp_path)
    assert parses == [], f"the second render re-parsed {parses}"
    assert second == first, "the remembered cards differ from the freshly read ones"


@pytest.mark.asyncio
async def test_two_page_renders_parse_each_file_once(config_dir, clean_registry, parses):
    """GET /presets twice: each file is parsed on the first render and NOTHING on the second.

    The page also reads ``nodes.yaml`` and the Scheduler's config (the reference scan behind the
    trashcan and the destination rule), so the first render's count covers those two documents as
    well; what the criterion is about is the PRESET files, and the second render parses nothing at all.
    """
    preset_file(config_dir, name="presets.yaml")
    preset_file(config_dir, SUMMER, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        first = await presets_html(http, f"{presets_page.PRESETS_PATH}?file=")
        assert len(preset_parses(parses)) == 2, f"the first render parsed {parses}"
        parses.clear()
        second = await presets_html(http, f"{presets_page.PRESETS_PATH}?file=")
    assert parses == [], f"the second render re-parsed {parses}"
    assert card_names(second) == card_names(first) == ["Spring", "Summer"]


# --------------------------------------- 2. a write from outside the page is seen on the next render

@pytest.mark.asyncio
async def test_a_file_written_outside_the_page_is_read_on_the_very_next_render(config_dir,
                                                                              clean_registry, parses):
    """``/mission save preset`` writes ``presets.yaml`` with a plain ``open(..., 'w')``.

    No console save happens between the two renders: the file's own mtime and size are the whole
    staleness story, which is exactly the question this test exists to answer.
    """
    path = preset_file(config_dir, name="presets.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        first = await presets_html(http)
        assert card_names(first) == ["Spring"]
        parses.clear()
        write_like_the_mission_command(path, SUMMER)
        second = await presets_html(http)
    assert card_names(second) == ["Summer"], "the page served the pre-write content"
    assert parses == [str(path)], f"the external write was not re-read: {parses}"


def test_an_outside_write_is_seen_without_a_page(tmp_path, parses):
    """The same question one layer down: the pure read, two writes, no console save anywhere."""
    path = preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    write_like_the_mission_command(path, SUMMER)
    cards, unreadable, _found = presets_page.read_presets(tmp_path)
    assert [card["name"] for card in cards] == ["Summer"]
    assert unreadable == []


# ----------------------------------------------------------- 3. the two halves of the key each count

def test_an_mtime_change_with_identical_content_reparses(tmp_path, parses):
    """A rewrite that leaves the bytes identical still re-parses: the mtime is part of the key."""
    path = preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    assert len(parses) == 1
    later = path.stat().st_mtime_ns + 5_000_000
    os.utime(path, ns=(later, later))
    cards, _unreadable, _found = presets_page.read_presets(tmp_path)
    assert len(parses) == 2, "a moved mtime was served from what was remembered"
    assert [card["name"] for card in cards] == ["Spring"]


def test_a_size_change_reparses(tmp_path, parses):
    """A longer file re-parses even when the clock is held still: the size is part of the key."""
    path = preset_file(tmp_path, name="presets.yaml")
    stamp = path.stat()
    presets_page.read_presets(tmp_path)
    assert len(parses) == 1
    write_like_the_mission_command(path, SPRING + "Autumn:\n  temperature: 3\n")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    cards, _unreadable, _found = presets_page.read_presets(tmp_path)
    assert len(parses) == 2, "a resized file was served from what was remembered"
    assert [card["name"] for card in cards] == ["Spring", "Autumn"]


# --------------------------------------------------------------------- 4. disappears, and comes back

def test_a_deleted_file_is_not_resurrected_and_one_that_returns_is_read_again(tmp_path, parses):
    """Discovery is the source of the keys: a file it no longer lists renders nothing."""
    path = preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    path.unlink()
    cards, unreadable, found = presets_page.read_presets(tmp_path)
    assert cards == [] and unreadable == [] and found is False, \
        "the deleted file was served from what was remembered"
    preset_file(tmp_path, SUMMER, name="presets.yaml")
    parses.clear()
    cards, _unreadable, _found = presets_page.read_presets(tmp_path)
    assert [card["name"] for card in cards] == ["Summer"]
    assert parses == [str(path)], "the file that reappeared was served from an older answer"


@pytest.mark.asyncio
async def test_a_deleted_file_leaves_the_page_empty(config_dir, clean_registry):
    """Through the app: the card goes away when the file does."""
    path = preset_file(config_dir, name="presets.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert card_names(await presets_html(http)) == ["Spring"]
        path.unlink()
        html = await presets_html(http)
    assert card_names(html) == []
    assert presets_page.PRESETS_NO_FILE in html


# ------------------------------------------------------------------ 5. a refusal is remembered as one

def test_an_unreadable_file_is_remembered_as_its_refusal(tmp_path, parses):
    """A broken file is parsed once and its refusal replayed — never retried on every render."""
    preset_file(tmp_path, "Spring:\n  date: [unclosed\n", name="presets.yaml")
    _cards, first, _found = presets_page.read_presets(tmp_path)
    assert len(parses) == 1
    assert first[0]["msgid"] == presets_page.PRESETS_UNREADABLE
    detail = first[0]["params"]["detail"]
    assert detail
    parses.clear()
    _cards, second, _found = presets_page.read_presets(tmp_path)
    assert parses == [], "an unreadable file was re-parsed on the next render"
    assert second == first, "the replayed refusal differs from the one that was remembered"


@pytest.mark.asyncio
async def test_the_refusals_sentence_still_renders_on_a_second_render(config_dir, clean_registry,
                                                                     parses):
    """The remembered refusal is replayed as its own sentence, on the page, every time."""
    preset_file(config_dir, "Spring:\n  date: [unclosed\n", name="presets.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        first = await presets_html(http)
        assert "config/presets.yaml could not be read as YAML" in first
        parses.clear()
        second = await presets_html(http)
    assert "config/presets.yaml could not be read as YAML" in second
    assert parses == [], f"the broken file was re-parsed: {parses}"


def test_a_file_that_is_not_a_map_is_remembered_as_its_refusal(tmp_path, parses):
    """The other refusal states are remembered the same way, with their own sentences."""
    preset_file(tmp_path, "- a\n- b\n", name="presets.yaml")
    preset_file(tmp_path, "{}\n", name="presets2.yaml")
    _cards, first, _found = presets_page.read_presets(tmp_path)
    assert [record["msgid"] for record in first] == [presets_page.PRESETS_NOT_A_MAP,
                                                     presets_page.PRESETS_EMPTY_FILE]
    parses.clear()
    _cards, second, _found = presets_page.read_presets(tmp_path)
    assert parses == []
    assert second == first


# ------------------------------------------------------------------------------- 6. both the bounds

def test_the_file_cap_evicts_the_least_recently_read(tmp_path, parses, monkeypatch):
    """Past the file cap the least recently READ answer goes; the others are not parsed again."""
    monkeypatch.setattr(presets_page, "PRESET_DOCUMENTS_MAX", 2)
    a = preset_file(tmp_path, "One:\n  temperature: 1\n", name="presets1.yaml")
    b = preset_file(tmp_path, "Two:\n  temperature: 2\n", name="presets2.yaml")
    c = preset_file(tmp_path, "Three:\n  temperature: 3\n", name="presets3.yaml")
    for path in (a, b, a):                       # a is the most recently read of the pair
        assert presets_page.read_preset_file(path).parsed
    assert len(presets_page._PRESET_DOCUMENTS) == 2
    assert presets_page.read_preset_file(c).parsed   # storing c evicts b, the least recently read
    assert len(presets_page._PRESET_DOCUMENTS) == 2, "the file cap did not hold"
    parses.clear()
    assert presets_page.read_preset_file(a).parsed
    assert presets_page.read_preset_file(c).parsed
    assert parses == [], f"a held file was parsed again: {parses}"
    assert presets_page.read_preset_file(b).parsed
    assert parses == [str(b)], f"the evicted file was not the least recently read: {parses}"


def test_a_document_over_the_budget_is_not_held(tmp_path, parses, monkeypatch):
    """A document larger than the byte budget is never held, so the cap cannot be blown."""
    monkeypatch.setattr(presets_page, "PRESET_DOCUMENTS_MAX_BYTES", 40)
    preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    assert presets_page._PRESET_DOCUMENTS == {}, "a document over the budget was held anyway"
    parses.clear()
    presets_page.read_presets(tmp_path)
    assert len(parses) == 1, "the unheld file was not read again"


def test_the_held_bytes_are_the_source_bytes_of_a_parsed_file(tmp_path, monkeypatch):
    """What the budget counts is the size of the file each document was parsed from."""
    path = preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    held = [entry[1] for entry in presets_page._PRESET_DOCUMENTS.values()]
    assert held == [path.stat().st_size]
    # a refusal is a sentence, not a document: it is held at zero weight
    preset_file(tmp_path, "Spring:\n  date: [unclosed\n", name="presets2.yaml")
    presets_page.read_presets(tmp_path)
    assert sorted(entry[1] for entry in presets_page._PRESET_DOCUMENTS.values()) == [0,
                                                                                    path.stat().st_size]


# ------------------------------------------------------ 7. the console's own invalidation is wired

def test_forgetting_makes_the_next_read_parse_again(tmp_path, parses):
    """``forget_preset_documents`` is the explicit half of the staleness story."""
    preset_file(tmp_path, name="presets.yaml")
    presets_page.read_presets(tmp_path)
    assert len(parses) == 1
    presets_page.forget_preset_documents()
    assert presets_page._PRESET_DOCUMENTS == {}
    parses.clear()
    presets_page.read_presets(tmp_path)
    assert len(parses) == 1


def test_both_save_exits_forget_the_remembered_documents():
    """A console save must not depend on a timestamp granularity: every save exit forgets by name.

    Counted call sites — the definition plus ONE forgotten-cache block per save exit:
    the area's whole-document save (``_presets_save``), the review step's expert save
    (``_save_expert``) and the one-block editor's save (``_presets_yaml_save``). A fourth save
    exit must add its own block and bump this pin.
    """
    text = (REPO_ROOT / "services" / "webservice" / "pages" / "presets.py").read_text(
        encoding="utf-8")
    assert text.count("forget_preset_documents()") == 4, \
        "expected the definition and THREE save exits"
    marker = "forget_node_lists(PRESET_LIST)\n        # and every remembered parse"
    assert text.count(marker) == 3, "a save exit forgets the preset list but not the documents"


# --------------------------------------------------------------------- 8. the write path stays fresh

def test_the_save_path_parses_its_own_before_and_candidate():
    """The mutating call sites read the file themselves — a shared document would be corrupted.

    Counted call sites: ``before`` is read by the two guarded exits (``_save_expert``'s collision
    check and ``_presets_save``'s before/guard pair count once each); ``candidate`` is read by all
    three saves — those two plus the one-block editor, which needs only the copy it mutates.
    """
    text = (REPO_ROOT / "services" / "webservice" / "pages" / "presets.py").read_text(
        encoding="utf-8")
    assert text.count("before = configwrite.load_config_file(path)") == 2, \
        "a save path no longer reads its own copy of the file"
    assert text.count("candidate = configwrite.load_config_file(path)") == 3, \
        "a save path no longer reads its own copy of the file"
