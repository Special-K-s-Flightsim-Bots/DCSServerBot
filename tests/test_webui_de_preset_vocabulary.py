"""The German preset vocabulary: one word family, ``Preset``/``Presets``, and nothing else.

The catalogs carry ONE German word for the English ``preset``: the singular ``Preset``, the plural
``Presets``, compounds with the same first element (``Preset-Datei``, ``Preset-Name``,
``Preset-Schlüssel``, ``Wetter-Preset``, ``DCS-Preset``, ``MizEdit-Presets``). The settings-flavoured
feminine noun that competed with it is gone from every German msgstr.

The page's own name is the ONE exception: the msgid ``Presets`` keeps ``Missions-Presets``, because
that label names mission presets and distinguishes them from application settings.

Read from the ``.po`` TEXT (so the check runs without Babel) and, for the served strings, from the
compiled ``.mo`` the way the runtime reads it — a filled ``.po`` beside a stale ``.mo`` renders the
previous word with nothing anywhere to signal it.
"""
from __future__ import annotations

import gettext
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CATALOGS = ("webui", "schema")
#: the word family the catalogs must carry, and the competing feminine noun they must not.
WORD = "Preset"
FORBIDDEN = "Voreinstellung"
#: the page's own name, pinned for the msgid ``Presets`` (decision: the label names mission presets).
LABEL_MSGID = "Presets"
LABEL_DE = "Missions-Presets"

#: a placeholder is gettext's ``%(name)s`` or the brace form ``{server}``.
_HOLE = re.compile(r"%\(\w+\)[sd]|\{\w+\}")


# --------------------------------------------------------------------------- the .po reader

def _unescape(literal: str) -> str:
    """The value of one double-quoted PO literal (the escapes a catalog actually uses)."""
    body = literal[1:-1] if literal.endswith('"') else literal[1:]
    return (body.replace('\\\\', '\\').replace('\\"', '"')
            .replace('\\n', '\n').replace('\\t', '\t'))


def entries(domain: str) -> list[dict[str, str]]:
    """Every entry of one German catalog as ``{"msgid": ..., "msgstr": ...}``, read from the text."""
    text = (REPO / "locale" / "de" / "LC_MESSAGES" / f"{domain}.po").read_text("utf-8")
    out: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        found: dict[str, str] = {}
        for keyword in ("msgid", "msgstr"):
            match = re.search(rf'^{keyword}\s+((?:"(?:[^"\\\\]|\\\\.)*"\s*)+)', block, re.M)
            if match is None:
                continue
            found[keyword] = "".join(_unescape(part) for part
                                     in re.findall(r'"(?:[^"\\]|\\.)*"', match.group(1)))
        if "msgid" in found:
            out.append({"msgid": found["msgid"], "msgstr": found.get("msgstr", "")})
    return out


def _holes(text: str) -> list[str]:
    return sorted(_HOLE.findall(text or ""))


def _served(domain: str) -> gettext.NullTranslations:
    return gettext.translation(domain, localedir=str(REPO / "locale"), languages=["de"])


# --------------------------------------------------------------------- (a) the word is gone

@pytest.mark.parametrize("domain", CATALOGS)
def test_no_german_msgstr_carries_the_other_word(domain):
    """No msgstr in the German catalog carries the competing feminine noun.

    The count of entries scanned is printed so a run states what it covered; an empty or truncated
    read would otherwise pass this check by finding nothing.
    """
    catalog = entries(domain)
    offenders = [e for e in catalog if FORBIDDEN in e["msgstr"]]
    print(f"{domain}.po: {len(catalog)} entries scanned, "
          f"{len(offenders)} carry {FORBIDDEN!r}")
    assert len(catalog) > 1000, f"{domain}.po: only {len(catalog)} entries read — the parse is broken"
    assert not offenders, (
        f"{domain}.po: {len(offenders)} msgstr still read the settings-flavoured word: "
        f"{[e['msgid'] for e in offenders][:5]}")


# --------------------------------------------------- (b) the page's own name is not the sweep

def test_the_page_s_own_label_keeps_its_missions_word():
    """The msgid ``Presets`` still serves ``Missions-Presets`` in German — it names mission presets."""
    labels = [e for e in entries("webui") if e["msgid"] == LABEL_MSGID]
    assert len(labels) == 1, f"webui.po: {len(labels)} entries carry the msgid {LABEL_MSGID!r}"
    assert labels[0]["msgstr"] == LABEL_DE, (
        f"the page's own name reads {labels[0]['msgstr']!r}, expected {LABEL_DE!r}")
    assert _served("webui").gettext(LABEL_MSGID) == LABEL_DE, (
        "the compiled catalog does not serve the page's own name")


# ------------------------------------------------- (c) the runtime really serves the new wording

#: one entry per touched catalog, with the German text the compiled catalog must serve.
SERVED = {
    "webui": {
        "New preset": "Neues Preset",
        "Preset name": "Preset-Name",
        "Weather preset": "Wetter-Preset",
        "Save preset": "Preset speichern",
        "No preset matches this filter.": "Kein Preset passt zu diesem Filter.",
        "Launched with a temporary weather preset":
            "Gestartet mit einem temporären Wetter-Preset",
    },
    "schema": {
        "Preset name.": "Preset-Name.",
        "Single terrain this preset applies to.":
            "Einzelnes Terrain, für das dieses Preset gilt.",
        "Preset file that defines all tournament presets.":
            "Preset-Datei, die alle Turnier-Presets definiert.",
    },
}


@pytest.mark.parametrize("domain", CATALOGS)
def test_the_compiled_catalog_serves_the_new_words(domain):
    """``gettext`` over the ``.mo`` returns the ``.po``'s new German — and never the English msgid."""
    served = _served(domain)
    for msgid, expected in SERVED[domain].items():
        assert served.gettext(msgid) == expected, (
            f"{domain}: {msgid!r} serves {served.gettext(msgid)!r}, expected {expected!r} — "
            "recompile the catalog")
        assert served.gettext(msgid) != msgid, f"{domain}: {msgid!r} serves the English msgid"


@pytest.mark.parametrize("domain", CATALOGS)
def test_the_mo_is_not_older_than_the_po(domain):
    """A ``.mo`` older than its ``.po`` renders the previous word with nothing to signal it."""
    directory = REPO / "locale" / "de" / "LC_MESSAGES"
    po, mo = directory / f"{domain}.po", directory / f"{domain}.mo"
    assert mo.stat().st_mtime + 2 >= po.stat().st_mtime, f"{domain}.mo is older than {domain}.po"


# ------------------------------------------------------- (d) no sentence lost or gained a hole

@pytest.mark.parametrize("domain", CATALOGS)
def test_no_preset_sentence_lost_a_placeholder(domain):
    """Every German entry that names a preset carries the msgid's placeholders, no more and no fewer.

    Placeholder parity is the defect class that renders a broken sentence while every count stays
    green: the sentence is translated, the holes are not, and only the formatted output is wrong.
    """
    touched = [e for e in entries(domain) if WORD in e["msgstr"]]
    print(f"{domain}.po: {len(touched)} German entries name a preset")
    assert touched, f"{domain}.po: no German entry names a preset — the sweep is missing"
    for entry in touched:
        assert _holes(entry["msgid"]) == _holes(entry["msgstr"]), (
            f"{domain}: {entry['msgid']!r} has holes {_holes(entry['msgid'])} but "
            f"{entry['msgstr']!r} has {_holes(entry['msgstr'])}")
