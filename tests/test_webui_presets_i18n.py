"""The Presets area's own sentences, translated in every shipped catalog (Presets-area slice S6).

The rest of the console is pinned by ``test_webui_i18n.py``; this module pins the sentences the
Presets area (``weather_fields.py``, ``pages/presets.py`` and the two ``presets*.html`` templates)
introduces. It reads each catalog the way the RUNTIME does — ``gettext.translation`` over the
compiled ``.mo`` — not the ``.po``, because a filled ``.po`` with a stale ``.mo`` renders English.
For every Presets-area sentence it asserts the served string DIFFERS from the msgid (a miss renders
the English msgid) and carries exactly the msgid's placeholders.

A handful of msgids are legitimately IDENTICAL in a target language — units and symbols written the
same everywhere (``m/s``, ``mmHg``, ``°C``) and loanwords the language spells the same (German
``Filter``/``Halo``, Spanish ``Base``/``Halo``/``manual``). ``gettext`` returns the msgid for those,
which is CORRECT; :data:`IDENTICAL` names them per language so the pin does not call a correct
translation missing. Nothing else is excused.
"""
from __future__ import annotations

import gettext
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
#: the source files whose strings make up the Presets area.
_PRESETS_SOURCES = ("pages/presets.py", "weather_fields.py", "presets_wizard.html", "presets.html")
#: the languages that ship a ``webui`` catalog.
_LANGUAGES = ("de", "es", "ru", "cn")
#: per language, the msgids whose translation is deliberately the msgid itself (units, symbols and
#: loanwords written the same in the target language). Each is a correct translation, not a miss.
IDENTICAL: dict[str, frozenset[str]] = {
    "de": frozenset({"Filter", "Halo", "m/s", "mmHg", "°C", "Preset"}),
    "es": frozenset({"Base", "Halo", "m/s", "manual", "mmHg", "°C"}),
    "ru": frozenset({"°C"}),
    "cn": frozenset({"m/s", "mmHg", "°C"}),
}

#: a placeholder is gettext's ``%(name)s`` (a Jinja ``{% trans %}`` body) or the brace form ``{name}``
#: the dialog sentence records carry. A bare ``%``/``{`` matching neither is not a placeholder.
_PLACEHOLDER = re.compile(r"%\((\w+)\)|\{(\w+)\}")


def _placeholders(text: str) -> dict[str, int]:
    """The placeholder names in *text* with their counts (a repeated name is meaningful)."""
    counts: dict[str, int] = {}
    for match in _PLACEHOLDER.finditer(text or ""):
        name = match.group(1) or match.group(2)
        counts[name] = counts.get(name, 0) + 1
    return counts


def _have_babel() -> bool:
    return importlib.util.find_spec("babel") is not None


def _presets_msgids_from_locations(po: Path) -> set[str]:
    """Every msgid the catalog maps to a Presets-area source file, read from the ``#:`` references."""
    from babel.messages.pofile import read_po
    with po.open("rb") as handle:
        catalog = read_po(handle)
    found: set[str] = set()
    for message in catalog:
        if not isinstance(message.id, str) or not message.id:
            continue
        for location in (message.locations or ()):
            path = location[0] if isinstance(location, tuple) else location
            if any(source in path for source in _PRESETS_SOURCES):
                found.add(message.id)
                break
    return found


def _po_unescape(chunks: str) -> str:
    text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', chunks))
    return (text.replace("\\\\", "\x00").replace('\\"', '"').replace("\\n", "\n")
            .replace("\\t", "\t").replace("\x00", "\\"))


def _presets_msgids_from_text(po: Path) -> set[str]:
    """The fallback: read the ``#:`` reference comments straight out of the ``.po`` text.

    Reached only when Babel is not installed. It walks the blank-line-separated entries, keeps the
    ones whose reference lines name a Presets-area file, and reads the entry's ``msgid``.
    """
    found: set[str] = set()
    for block in re.split(r"\n\s*\n", po.read_text("utf-8")):
        refs = [line for line in block.splitlines() if line.startswith("#:")]
        if not refs:
            continue
        if not any(source in line for line in refs for source in _PRESETS_SOURCES):
            continue
        match = re.search(r'^msgid\s+((?:"(?:[^"\\]|\\.)*"\s*)+)', block, re.M)
        if match:
            value = _po_unescape(match.group(1))
            if value:
                found.add(value)
    return found


def _presets_msgids() -> set[str]:
    po = REPO / "locale" / "de" / "LC_MESSAGES" / "webui.po"
    assert po.is_file(), f"missing catalog: {po}"
    return (_presets_msgids_from_locations(po) if _have_babel()
            else _presets_msgids_from_text(po))


def _load(language: str) -> gettext.NullTranslations:
    """The catalog loaded the way the runtime loads it: ``gettext.translation`` over the ``.mo``."""
    return gettext.translation("webui", localedir=str(REPO / "locale"), languages=[language],
                               fallback=True)


def test_the_presets_area_sentences_are_translated_in_every_catalog():
    """Every Presets-area sentence serves a translation that DIFFERS from the msgid, in every language.

    A miss — an empty ``msgstr``, a ``#, fuzzy`` entry gettext dropped, or a stale ``.mo`` — makes
    ``gettext`` hand back the English msgid, so the served string equals the msgid and this fails.
    The check runs over the COMPILED catalog, which is what the console renders from.
    """
    msgids = _presets_msgids()
    assert len(msgids) >= 145, (
        f"only {len(msgids)} Presets-area msgids were derived from the catalog references — the "
        "derivation is broken, not the translations")

    for language in _LANGUAGES:
        translations = _load(language)
        for msgid in sorted(msgids):
            if msgid in IDENTICAL[language]:
                continue
            served = translations.gettext(msgid)
            assert served != msgid, (
                f"{language}: the Presets-area sentence {msgid!r} renders the English msgid — its "
                f"msgstr is empty, fuzzy, or the .mo was not recompiled")
            assert _placeholders(served) == _placeholders(msgid), (
                f"{language}: the translation of {msgid!r} changes its placeholders — "
                f"msgid {sorted(_placeholders(msgid).items())} vs "
                f"served {sorted(_placeholders(served).items())}")


def test_the_identical_set_is_honest():
    """Every msgid :data:`IDENTICAL` excuses really is served as its own msgid in that language.

    The positive control for the exemption: if a translation later differs, the excuse is stale and
    is reported here rather than silently widening the blind spot of the pin above.
    """
    msgids = _presets_msgids()
    for language, excused in IDENTICAL.items():
        translations = _load(language)
        for msgid in excused:
            assert msgid in msgids, f"{language}: {msgid!r} is excused but not a Presets-area msgid"
            assert translations.gettext(msgid) == msgid, (
                f"{language}: {msgid!r} is excused as identical but now differs — drop the excuse")


def test_the_pin_fires_on_an_untranslated_entry(tmp_path):
    """A synthetic catalog with one empty msgstr is reported — the pin above cannot pass vacuously.

    Builds a real ``.mo`` under a temp ``localedir``, loads it through the SAME reader the pin uses,
    and asserts the check that the pin runs flags the missing translation.
    """
    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo

    catalog = Catalog(locale="xx")
    catalog.add("Served and translated", "Servido y traducido")
    catalog.add("Served and missing", "")
    localedir = tmp_path / "locale"
    target = localedir / "xx" / "LC_MESSAGES"
    target.mkdir(parents=True)
    with (target / "webui.mo").open("wb") as handle:
        write_mo(handle, catalog)

    translations = gettext.translation("webui", localedir=str(localedir), languages=["xx"])
    assert translations.gettext("Served and translated") != "Served and translated"
    assert translations.gettext("Served and missing") == "Served and missing", (
        "the reader did not hand back the msgid for an empty msgstr (the check would pass vacuously)")
