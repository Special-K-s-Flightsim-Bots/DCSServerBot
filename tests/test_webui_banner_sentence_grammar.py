"""The waiting-update banner sentence has ONE shape at every count, in every catalog.

The website has no plural support: a msgid is rendered through ``%(...)s`` interpolation, so a
sentence carrying a verb that agrees with the figure reads wrong at one of the counts. German made
that visible — "1 Knoten installieren" — but the class is language-independent, so this module pins
the SHAPE (the same wording at one and at two nodes), the exact rendered sentence in English and in
German, and every catalog entry against the compiled ``.mo`` the runtime actually reads.
"""
from __future__ import annotations

import gettext
import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import dcs_update, i18n, readmodels
from services.webservice.pages import dashboard as dashboard_page
from test_webui_banner_update import (BRANCH, INSTALLED, LATEST, banner_of, cluster, declared,
                                      process_declares, waiting)
from test_webui_dashboard import BOT_YAML, Status, _build, _login, server
from test_webui_i18n import _catalog_pairs

REPO = Path(dashboard_page.__file__).resolve().parent.parent.parent.parent
LANGUAGES = ("de", "es", "ru", "cn")

MASTER = "dcs-a-01"
OTHER = "dcs-b-02"

BANNER_ITEM = re.compile(r'<li class="banner-item">(.*?)</li>', re.S)
TAG = re.compile(r"<[^>]*>")

#: The two sentences this card owns, exactly as the module declares them.
AUTO_MSGID = ("DCS update waiting on %(count)d node(s), installed on its own within the "
              "auto-update window: %(names)s")
MANUAL_MSGID = ("DCS update waiting on %(count)d node(s), auto-update is off, so a manual start "
                "is needed: %(names)s")

#: Finite verb forms that agree with a PLURAL count in German. None may appear in the sentence at
#: count=1 — the case an operator meets most often, and the one the sentence has to survive.
#: ``werden`` is deliberately absent: it is the auxiliary of the passive infinitive ("gestartet
#: werden") here, which agrees with nothing.
PLURAL_VERB_FORMS = ("installieren", "haben", "müssen", "starten", "sind")


# ----------------------------------------------------------------------------------- test doubles

@pytest.fixture(autouse=True)
def clean_dcs_cache():
    """Every pin starts with an EMPTY cache: an answer is only ever what the pin seeded."""
    dcs_update.reset()
    yield
    dcs_update.reset()


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def waiting_on(*names: str) -> None:
    for name in names:
        dcs_update.record(name, has_dcs=True, branch=BRANCH, version=INSTALLED, latest=LATEST,
                          available=True)


def app_over(config_dir, monkeypatch, *names: str, automatic: tuple[str, ...] = ()):
    """The dashboard app over *names*, all of them with a waiting update, in the given mode."""
    waiting_on(*names)
    blocks = process_declares(monkeypatch, declared(*names, automatic=automatic))
    source = cluster(*names, blocks=blocks)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source
    return app


async def rendered(config_dir, monkeypatch, language: str, *names: str, automatic=()):
    """The banner's item sentences, rendered by the real page in *language*."""
    app = app_over(config_dir, monkeypatch, *names, automatic=automatic)
    async with asgi_client(app) as client:
        await _login(client)
        client.cookies.set(i18n.COOKIE_NAME, language)
        response = await client.get(dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200, response.status_code
    items = BANNER_ITEM.findall(banner_of(response.text))
    assert items, "the page rendered no banner item"
    return [TAG.sub("", item).strip() for item in items]


def compiled(language: str) -> gettext.GNUTranslations:
    return gettext.translation(i18n.DOMAIN, localedir=str(REPO / "locale"), languages=[language])


def shape(sentence: str, count: int, names: str) -> str:
    """*sentence* with its figure and its name list blanked — the wording that must not vary."""
    return (sentence.replace(str(count), "\x00", 1) if sentence.count(str(count)) else sentence
            ).replace(names, "\x01")


# ------------------------------------------------------------------ the rendered sentence, both modes

@pytest.mark.asyncio
async def test_english_renders_one_node_and_two_nodes_with_the_same_wording(config_dir, monkeypatch):
    """ENGLISH, auto-update ON: the exact sentence at count=1 and at count=2."""
    one = await rendered(config_dir, monkeypatch, "en", MASTER, automatic=(MASTER,))
    two = await rendered(config_dir, monkeypatch, "en", MASTER, OTHER,
                         automatic=(MASTER, OTHER))

    assert one == ["DCS update waiting on 1 node(s), installed on its own within the "
                   "auto-update window: dcs-a-01"], one
    assert two == ["DCS update waiting on 2 node(s), installed on its own within the "
                   "auto-update window: dcs-a-01, dcs-b-02"], two


@pytest.mark.asyncio
async def test_english_renders_the_manual_mode_at_one_and_two_nodes(config_dir, monkeypatch):
    """ENGLISH, auto-update OFF: a control has to be pressed, stated the same way at both counts."""
    one = await rendered(config_dir, monkeypatch, "en", MASTER)
    two = await rendered(config_dir, monkeypatch, "en", MASTER, OTHER)

    assert one == ["DCS update waiting on 1 node(s), auto-update is off, so a manual start is "
                   "needed: dcs-a-01"], one
    assert two == ["DCS update waiting on 2 node(s), auto-update is off, so a manual start is "
                   "needed: dcs-a-01, dcs-b-02"], two


@pytest.mark.asyncio
async def test_german_renders_one_node_and_two_nodes_without_a_plural_verb(config_dir, monkeypatch):
    """GERMAN, auto-update ON: the exact sentence at count=1, and no verb agreeing with the count."""
    one = await rendered(config_dir, monkeypatch, "de", MASTER, automatic=(MASTER,))
    two = await rendered(config_dir, monkeypatch, "de", MASTER, OTHER,
                         automatic=(MASTER, OTHER))

    assert one == ["Wartendes DCS-Update auf 1 Knoten, wird im Auto-Update-Fenster selbst "
                   "installiert: dcs-a-01"], one
    assert two == ["Wartendes DCS-Update auf 2 Knoten, wird im Auto-Update-Fenster selbst "
                   "installiert: dcs-a-01, dcs-b-02"], two

    plural = [form for form in PLURAL_VERB_FORMS if form in one[0]]
    assert plural == [], f"the count=1 sentence carries a plural verb form: {plural} in {one[0]!r}"
    assert "installiert" in one[0], "the singular verb must be there, agreeing with the UPDATE"


@pytest.mark.asyncio
async def test_german_renders_the_manual_mode_at_one_and_two_nodes(config_dir, monkeypatch):
    """GERMAN, auto-update OFF: the same shape at both counts, the figure merely swapped."""
    one = await rendered(config_dir, monkeypatch, "de", MASTER)
    two = await rendered(config_dir, monkeypatch, "de", MASTER, OTHER)

    assert one == ["Wartendes DCS-Update auf 1 Knoten, Auto-Update ist aus, es muss manuell "
                   "gestartet werden: dcs-a-01"], one
    assert two == ["Wartendes DCS-Update auf 2 Knoten, Auto-Update ist aus, es muss manuell "
                   "gestartet werden: dcs-a-01, dcs-b-02"], two

    plural = [form for form in PLURAL_VERB_FORMS if form in one[0]]
    assert plural == [], f"the count=1 sentence carries a plural verb form: {plural} in {one[0]!r}"


# ------------------------------------------------------------------ the shape holds in every catalog

def test_no_catalog_sentence_changes_wording_between_one_and_two_nodes():
    """SHAPE INVARIANCE, all five languages: only the figure and the name list may differ.

    This is the language-independent form of the defect: whatever the wording, the sentence the
    operator reads at one node must be the sentence they read at two, with the figure swapped.
    """
    sentences: list[tuple[str, str, str]] = [("en", AUTO_MSGID, MANUAL_MSGID)]
    for language in LANGUAGES:
        catalog = compiled(language)
        sentences.append((language,
                          catalog.gettext(AUTO_MSGID),
                          catalog.gettext(MANUAL_MSGID)))

    checked = 0
    for language, automatic, manual in sentences:
        for template in (automatic, manual):
            one = template % {"count": 1, "names": MASTER}
            two = template % {"count": 2, "names": f"{MASTER}, {OTHER}"}
            checked += 1
            assert shape(one, 1, MASTER) == shape(two, 2, f"{MASTER}, {OTHER}"), (
                f"{language}: the wording changes with the count — {one!r} vs {two!r}")
    assert checked == 10, checked


# ------------------------------------------------------------------ the catalog the runtime reads

def test_every_catalog_entry_resolves_with_the_placeholder_multiset_of_its_msgid():
    """The compiled ``.mo`` RUNTIME lookup: translated, and every placeholder kept."""

    def placeholders(text: str) -> set[str]:
        return set(re.findall(r"%\((\w+)\)", text))

    compared = 0
    mismatches = []
    for language in LANGUAGES:
        catalog = compiled(language)
        for msgid in (AUTO_MSGID, MANUAL_MSGID):
            compared += 1
            translated = catalog.gettext(msgid)
            if translated == msgid:
                mismatches.append(f"{language}: {msgid!r} is untranslated")
            elif placeholders(translated) != placeholders(msgid):
                mismatches.append(f"{language}: {msgid!r} -> {translated!r} loses a placeholder")
    assert compared == 8, compared
    assert mismatches == [], mismatches


def test_the_new_entries_are_inserted_by_hand_and_compiled_after_the_catalog():
    """Each changed ``.po`` carries the entry AND its ``.mo`` beside it is fresher."""
    for language in LANGUAGES:
        meta = REPO / "locale" / language / "LC_MESSAGES"
        pairs = dict(_catalog_pairs(language))
        assert set(pairs) >= {AUTO_MSGID, MANUAL_MSGID}, f"{language}: entry missing"
        assert all(pairs[msgid] for msgid in (AUTO_MSGID, MANUAL_MSGID)), (
            f"{language}: the entry is an untranslated stub")
        assert (meta / "webui.mo").stat().st_mtime + 2 >= (meta / "webui.po").stat().st_mtime, (
            f"{language}: webui.mo is older than webui.po — the catalog was not recompiled")


def test_the_two_msgid_constants_are_the_sentences_the_banner_appends():
    """The module's own constants are the msgids every catalog above was checked against."""
    assert dashboard_page.UPDATE_AUTO_MSGID == AUTO_MSGID
    assert dashboard_page.UPDATE_MANUAL_MSGID == MANUAL_MSGID


# ------------------------------------------------------------------ the sentences this card must not touch

#: The four fault sentences, as pinned BEFORE this card: (msgid, {language: msgstr}). They are noun
#: phrases with no verb, which is exactly why they survive a count of one — nothing here may move.
FAULT_SENTENCES = {
    "%(count)d node(s) not heartbeating: %(names)s": {
        "de": "%(count)d Knoten ohne Heartbeat: %(names)s",
        "es": "%(count)d nodo(s) sin señal: %(names)s",
        "ru": "Узлов без heartbeat: %(count)d: %(names)s",
        "cn": "%(count)d 个节点未发送心跳：%(names)s"},
    "%(count)d server(s) down: %(names)s": {
        "de": "%(count)d Server ausgefallen: %(names)s",
        "es": "%(count)d servidor(es) caído(s): %(names)s",
        "ru": "Серверов выключено: %(count)d: %(names)s",
        "cn": "%(count)d 台服务器下线：%(names)s"},
    "%(count)d server(s) paused: %(names)s": {
        "de": "%(count)d Server pausiert: %(names)s",
        "es": "%(count)d servidor(es) en pausa: %(names)s",
        "ru": "Серверов на паузе: %(count)d: %(names)s",
        "cn": "%(count)d 台服务器已暂停：%(names)s"},
    "%(count)d ERROR line(s) in the visible log": {
        "de": "%(count)d ERROR-Zeile(n) im sichtbaren Protokoll",
        "es": "%(count)d línea(s) ERROR en el registro visible",
        "ru": "Строк с ERROR в видимом журнале: %(count)d",
        "cn": "可见日志中有 %(count)d 行 ERROR"},
}


@pytest.mark.asyncio
async def test_the_fault_banner_sentences_render_byte_identically(config_dir, tmp_path):
    """The fault classes are NOUN PHRASES and were already count-safe: they must not have moved."""
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "2026-10-26 10:00:01.000 ERROR\tLua export failed for Training\n", encoding="utf-8")
    source = readmodels.LiveSource(
        servers=(server(name="Maverick Ops", status=Status.SHUTDOWN, node="dcs-other-01"),
                 server(name="Training", status=Status.PAUSED, node=MASTER)),
        instances=(),
        nodes={MASTER: types.SimpleNamespace(name=MASTER, master=True, instances={}),
               "dcs-dead-02": None},
        node=types.SimpleNamespace(name=MASTER, master=True, instances={}),
        declared_dcs=declared(MASTER), log_path=log_file)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source

    banner = dashboard_page.attention(readmodels.overview(source))
    faults = [item["text"] for item in banner["alerts"] if item["kind"] != "update"]
    assert faults == [
        "1 node(s) not heartbeating: dcs-dead-02",
        "1 server(s) down: Maverick Ops",
        "1 server(s) paused: Training",
        "1 ERROR line(s) in the visible log",
    ], faults

    accepted = {
        "en": tuple(faults),
        "de": ("1 Knoten ohne Heartbeat: dcs-dead-02",
               "1 Server ausgefallen: Maverick Ops",
               "1 Server pausiert: Training",
               "1 ERROR-Zeile(n) im sichtbaren Protokoll"),
    }
    for language, expected in accepted.items():
        async with asgi_client(app) as client:
            await _login(client)
            client.cookies.set(i18n.COOKIE_NAME, language)
            response = await client.get(dashboard_page.DASHBOARD_PATH)
        assert response.status_code == 200
        rendered_items = [TAG.sub("", item).strip()
                          for item in BANNER_ITEM.findall(banner_of(response.text))]
        assert tuple(rendered_items[:4]) == expected, (language, rendered_items)

    for msgid, translations in FAULT_SENTENCES.items():
        for language, expected in translations.items():
            assert compiled(language).gettext(msgid) == expected, f"{language}: {msgid!r} moved"
