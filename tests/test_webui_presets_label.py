"""The Presets page's own NAME — one label, five surfaces, five languages.

The page names itself in exactly five places, and all five are fed by ONE constant
(``pages.presets.PRESETS_TITLE``):

* the SIDEBAR entry (``nav_items`` — the menu label the operator clicks),
* the browser tab's title (``templates/presets.html``'s ``title`` block),
* the page heading (``presets.html``'s ``<h1>``, from ``page_title``),
* the breadcrumb (``dashboard_page.crumb`` chrome, translated per request),
* the breadcrumb of the wizard the page opens (the same chrome word, plus the preset's NAME
  printed verbatim after it).

Because it is one constant, the menu entry and the page's own name cannot drift apart; the render
test below pins that on the RENDERED document in every shipped language, which is the half a
constant cannot prove.

The word is a CONTRACT, not a translator's preference: this label names presets FOR MISSIONS, so a
word that reads like application settings (German ``Voreinstellungen``, Spanish ``Preajustes``,
Russian ``Предустановки``, Chinese ``预设``) is wrong here even though it is a correct translation
of the English msgid. :data:`EXPECTED` carries the word per language, so a catalog that reverts to
the settings-flavoured adjective fails here instead of drifting back silently.
"""
from __future__ import annotations

import gettext
import re
from pathlib import Path

import pytest

from services.webservice import i18n
from services.webservice.pages import presets as presets_page
# the write suites' harness, reused so this module drives the REAL page through the REAL application
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, sign_in,
)

REPO = Path(__file__).resolve().parent.parent

#: the English source string — the msgid, never a translation.
SOURCE = "Presets"

#: the label each language must RENDER, as chosen by the owner (see the module docstring).
EXPECTED: dict[str, str] = {
    "en": "Presets",
    "de": "Missions-Presets",
    "es": "Preajustes de misión",
    "ru": "Пресеты миссий",
    "cn": "任务预设",
}

#: the settings-flavoured word each catalog carried for this label before the rename. Two of them
#: survive inside the chosen word (Spanish "Preajustes" leads "Preajustes de misión"), so the check
#: below compares each surface's WHOLE text — it asks "is this surface still the old word", not
#: "does the old substring appear anywhere".
OLD_WORD: dict[str, str] = {
    "de": "Voreinstellungen",
    "es": "Preajustes",
    "ru": "Предустановки",
    "cn": "预设",
}

#: a preset file, so the page renders in its normal (non-empty) state.
PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
"""

_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_H1 = re.compile(r"<h1>(.*?)</h1>", re.S)
_CRUMB = re.compile(r'<div class="crumb">(.*?)</div>', re.S)
_NAV = re.compile(r'<a [^>]*href="/presets">.*?</span>([^<]*)</a>', re.S)
_HOLE = re.compile(r"%\(\w+\)|\{\w+\}")


# --------------------------------------------------------------------------------- the readers

def _po_entry(language: str) -> tuple[str, str]:
    """The ``(msgid, msgstr)`` of the label entry in one catalog, read from the ``.po`` text.

    Read by hand rather than through Babel so the check runs on every machine: the block is found by
    its own ``msgid "Presets"`` line, and the two logical values are joined from their continuation
    lines the way gettext writes them.
    """
    text = (REPO / "locale" / language / "LC_MESSAGES" / "webui.po").read_text("utf-8")
    blocks = [block for block in re.split(r"\n\s*\n", text)
              if re.search(r'^msgid "Presets"$', block, re.M)]
    assert len(blocks) == 1, f"{language}: {len(blocks)} entries carry the label msgid, expected 1"
    block = blocks[0]

    def field(keyword: str) -> str:
        match = re.search(rf'^{keyword}\s+((?:"(?:[^"\\]|\\.)*"\s*)+)', block, re.M)
        assert match, f"{language}: the label entry has no {keyword}"
        return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))).replace('\\"', '"')

    return field("msgid"), field("msgstr")


def _served(language: str) -> str:
    """The label the RUNTIME serves: ``gettext`` over the compiled ``.mo``, as every page renders it."""
    translations = gettext.translation("webui", localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    return translations.gettext(SOURCE)


def _one(pattern: re.Pattern, html: str, what: str) -> str:
    """The whitespace-collapsed text of the ONE match of *pattern* in *html*."""
    match = pattern.search(html)
    assert match, f"the page rendered no {what}"
    return " ".join(match.group(1).split())


async def _page_html(http, language: str) -> str:
    """The Presets page as the APPLICATION renders it for *language* (a signed-in Admin's view)."""
    await sign_in(http, "specialk")            # Admin: holds presets.view
    http.cookies.set(i18n.COOKIE_NAME, language)
    response = await http.get(presets_page.PRESETS_PATH)
    assert response.status_code == 200, response.text
    return response.text


# ------------------------------------------------------- 1. one string, so two names cannot drift

def test_the_menu_entry_and_the_page_s_own_name_are_one_string():
    """The pinned assertion: the menu entry IS the page's own name, not a second spelling of it.

    The sidebar label, the tab title, the heading and the breadcrumb all read this one constant, so
    the menu can never name the page something the page does not call itself.
    """
    presets_page.declare()
    assert presets_page.PRESETS_TITLE == SOURCE, (
        "the label's English source moved: the msgid is the source string, every translation hangs "
        "off it")
    assert [item.label for item in presets_page.nav_items()] == [presets_page.PRESETS_TITLE], (
        f"the menu entry is not the page's own name: nav "
        f"{[item.label for item in presets_page.nav_items()]!r} vs {presets_page.PRESETS_TITLE!r}")
    # the wizard's breadcrumb reuses the SAME chrome word, ahead of the preset's own name
    from services.webservice.pages import dashboard as dashboard_page
    wizard_crumb = dashboard_page.crumb("Group", presets_page.PRESETS_TITLE, "Spring")
    assert wizard_crumb[1] == (presets_page.PRESETS_TITLE, True)


# --------------------------------------------- 2. every self-naming surface, on the real document

@pytest.mark.asyncio
@pytest.mark.parametrize("language", sorted(EXPECTED))
async def test_every_self_naming_surface_renders_the_chosen_word(language, config_dir, clean_registry):
    """The menu entry, the tab title, the heading and the breadcrumb all read the chosen word.

    Four surfaces on ONE rendered document: a per-request language that reached the sidebar but not
    the heading (or not the ``<title>``) would leave the page naming itself two things at once.
    """
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    app = build(config_dir, fleet())
    expected = EXPECTED[language]

    async with client(app) as http:
        html = await _page_html(http, language)

    assert _one(_NAV, html, "sidebar entry for the Presets page") == expected, (
        f"{language}: the menu entry does not read {expected!r}")
    assert _one(_H1, html, "page heading") == expected, (
        f"{language}: the heading does not read {expected!r}")
    assert _one(_TITLE, html, "page title").startswith(expected), (
        f"{language}: the browser tab's title does not read {expected!r}")
    assert _one(_CRUMB, html, "breadcrumb").split(" / ")[-1] == expected, (
        f"{language}: the breadcrumb's last chrome word does not read {expected!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("language", sorted(OLD_WORD))
async def test_the_settings_flavoured_word_is_gone_from_the_page_s_own_name(language, config_dir,
                                                                          clean_registry):
    """The control: no surface that names this page still reads the old settings-flavoured word."""
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    app = build(config_dir, fleet())
    old = OLD_WORD[language]

    async with client(app) as http:
        html = await _page_html(http, language)

    for surface, pattern in (("sidebar", _NAV), ("heading", _H1),
                             ("page title", _TITLE), ("breadcrumb", _CRUMB)):
        text = _one(pattern, html, surface)
        assert text != old and not text.startswith(f"{old} —"), (
            f"{language}: the {surface} still reads the old word {old!r} ({text!r})")


# ------------------------------------------------------------------ 3. the catalogs and the .mo

#: the languages that ship a TRANSLATED catalog. English is the source language and the msgid, so it
#: ships none: its side of the label is asserted through the lookup, never through a file.
TRANSLATED: tuple[str, ...] = ("cn", "de", "es", "ru")


def test_english_serves_the_msgid_itself_and_ships_no_catalog():
    """English renders the SOURCE ITSELF — gettext falls back to the msgid — and no ``en`` catalog exists.

    The label is a contract, so the English side is pinned too: an identity catalog cannot be read
    here (there is none), and a catalog that translated the source would make English serve another
    label's words instead of ``Presets``.
    """
    assert _served("en") == SOURCE, (
        f"English renders {_served('en')!r} for the label: English is the msgid, so it serves {SOURCE!r}")
    assert not (REPO / "locale" / "en").exists(), (
        f"an English catalog exists at {REPO / 'locale' / 'en'}: English is the source language and "
        "the msgid, so it ships none")


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_catalog_carries_the_chosen_word(language):
    """Every translated catalog's label entry holds the chosen word."""
    msgid, msgstr = _po_entry(language)
    assert msgid == SOURCE, f"{language}: the msgid is {msgid!r}, expected the English source"
    assert msgstr == EXPECTED[language], (
        f"{language}: the label entry reads {msgstr!r}, expected {EXPECTED[language]!r}")


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_mo_serves_the_po_and_is_not_older(language):
    """The COMPILED catalog is what renders: it must carry the ``.po``'s word and be no older.

    A filled ``.po`` beside a stale ``.mo`` renders the previous word with nothing anywhere to
    signal it, which is exactly how a rename reaches half the pages.
    """
    _msgid, msgstr = _po_entry(language)
    assert _served(language) == msgstr, (
        f"{language}: the .mo serves {_served(language)!r} while the .po carries {msgstr!r} — "
        "recompile it")
    po = REPO / "locale" / language / "LC_MESSAGES" / "webui.po"
    mo = po.with_suffix(".mo")
    assert mo.stat().st_mtime + 2 >= po.stat().st_mtime, f"{language}: {mo} is older than {po}"


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_touched_entry_keeps_its_placeholders(language):
    """Placeholder parity for the touched entry: it carries no hole, so no side may invent one."""
    msgid, msgstr = _po_entry(language)
    assert _HOLE.findall(msgid) == _HOLE.findall(msgstr) == [], (
        f"{language}: the label gained a placeholder on one side only ({msgid!r} vs {msgstr!r})")
