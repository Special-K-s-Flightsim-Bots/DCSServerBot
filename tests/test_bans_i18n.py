"""The scoped-unban sentences live in every shipped admin and mission catalog AND survive gettext.

The ban work replaced the bot-wide ``Player {} unbanned on all servers.`` sentence with a set of
scoped ones. Two invariants keep them from silently regressing to English:

* each ``.po`` carries the new ``msgid`` (source half);
* the COMPILED ``.mo`` resolves it to something other than the msgid (runtime half) — a ``.po`` can
  carry a sentence its stale ``.mo`` never serves, which is exactly what happens when nobody
  recompiles after an edit.

Each translation must keep the msgid's two ``{}`` placeholders, or the server name vanishes at
render time.
"""
from __future__ import annotations

import gettext
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LANGUAGES = ("cn", "de", "es", "ru")
CATALOGS = {
    "admin": ("Player {} unbanned on {}.", "Player {} was not banned on {}."),
    "mission": ("User {} unbanned on {}.", "User {} was not banned on {}."),
}
PARAMS = [(domain, language) for domain in sorted(CATALOGS) for language in LANGUAGES]


@pytest.mark.parametrize("domain,language", PARAMS)
def test_each_catalog_carries_the_scoped_unban_msgids(domain, language):
    path = REPO / "locale" / language / "LC_MESSAGES" / f"{domain}.po"
    text = path.read_text(encoding="utf-8")
    for msgid in CATALOGS[domain]:
        assert f'msgid "{msgid}"' in text, f"{language} {domain}.po lacks {msgid!r}"


@pytest.mark.parametrize("domain,language", PARAMS)
def test_the_compiled_catalog_resolves_the_scoped_unban_msgids(domain, language):
    translations = gettext.translation(domain, localedir=str(REPO / "locale"),
                                       languages=[language], fallback=True)
    for msgid in CATALOGS[domain]:
        resolved = translations.gettext(msgid)
        assert resolved != msgid, f"{language} {domain}: {msgid!r} still renders English"
        assert resolved.count("{}") == msgid.count("{}"), \
            f"{language} {domain}: {msgid!r} translation drops a placeholder: {resolved!r}"
