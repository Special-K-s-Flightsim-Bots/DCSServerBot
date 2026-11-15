"""The Language step's prefill: the browser's language, the operator's saved answer, and the switch.

TWO LAYERS, both over the shipped code:

* :func:`services.webservice.setup.language.browser_language` is driven directly with the header
  shapes a browser really sends (q-values, several tags, regional and script variants, garbage);
* the FIRST layer — which answer step 1 marks, and in which language the tour renders — is driven
  through a real ASGI request against the shipped setup application, so the precedence (explicit
  switch -> saved draft -> browser -> install default) is the shipped one.

The reported case (a German browser on a fresh install) is here as its own test: it must preselect
German.
"""
from __future__ import annotations

import re
import types
from html import unescape

import pytest

from conftest import asgi_client
from services.webservice import i18n
from services.webservice.setup import language as language_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH, T_LANG_SOURCE
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
ON_RE = re.compile(r'class="mi on" role="menuitem" hreflang="([^"]+)"')
HTML_LANG_RE = re.compile(r'<html lang="([^"]+)"')
SOURCE_RE = re.compile(r'<p class="source">(.*?)</p>', re.S)

#: the German screen title of step 1 — proof the tour really rendered in German, not just marked it
GERMAN_TITLE = "Welche Sprache sprichst Du?"

#: the header a German browser sends, verbatim — quoted back by the browser sentence
GERMAN_HEADER = "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"

#: the two English sentences step 1 shows on an English page (the other three are shown on a page in
#: the preselected language itself, so they are compared against the catalog — see :func:`says`)
SAYS_ANSWER = "Preselected from the answer you saved to this step on an earlier run."
SAYS_DEFAULT = "Preselected from this install's default language — nothing else named one."


def says(language: str, source: str, **params) -> str:
    """A step sentence as the SHIPPED catalog renders it — the same lookup the page itself makes.

    The sentence is drawn in the language the step preselected, so a case whose source is the browser
    or the cookie is compared in THAT language, never in English.
    """
    return i18n.translations_for(language).gettext(T_LANG_SOURCE[source]).format(**params)


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def marked(html: str) -> str | None:
    found = ON_RE.search(html)
    return found.group(1) if found else None


def rendered(html: str) -> str | None:
    found = HTML_LANG_RE.search(html)
    return found.group(1) if found else None


def source_line(html: str) -> str:
    """The sentence step 1 shows about where its preselection came from; fails when there is none."""
    found = SOURCE_RE.search(html)
    assert found, "the Language step shows no sentence about where its preselection came from"
    return unescape(found.group(1)).strip()


class _StubRequest:
    """The little the resolver reads off a request: query, cookies, one header, the app default."""

    def __init__(self, *, lang=None, cookie=None, header=None, default=None):
        self.query_params = {"lang": lang} if lang else {}
        self.cookies = {i18n.COOKIE_NAME: cookie} if cookie else {}
        self.headers = {"accept-language": header} if header else {}
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(webui_default_language=default))


# --------------------------------------------------------------- the browser tag parser (layer 1)

SUPPORTED = ("en", "cn", "de", "es", "ru")


@pytest.mark.parametrize("header, expected", [
    # the header a German browser sends, verbatim
    ("de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7", "de"),
    ("de", "de"),
    ("DE-DE", "de"),                     # case-folded
    ("de_AT", "de"),                     # the separator is not significant
    ("de-AT", "de"),                     # region dropped
    ("de-CH,de;q=0.9", "de"),
    ("de-CH;q=1.0", "de"),
    ("es-419,es;q=0.9", "es"),
    ("ru-RU", "ru"),
    ("en-GB", "en"),
    # the Chinese family: browsers say zh, this project's catalog is `cn`
    ("zh", "cn"),
    ("zh-CN", "cn"),
    ("zh-SG", "cn"),
    ("zh-TW", "cn"),
    ("zh-HK", "cn"),
    ("zh-Hans", "cn"),
    ("zh-Hant", "cn"),
    ("zh-Hant-TW", "cn"),
    ("zh-CN,zh;q=0.9,en;q=0.8", "cn"),
    ("cn", "cn"),                        # the project's own code is accepted too
    # q-values reorder the list
    ("en;q=0.4,de;q=0.9", "de"),
    ("sv,de;q=0.5", "de"),               # an unsupported tag is skipped, not fatal
    ("de;q=0.0,en;q=0.5", "en"),         # q=0 means "not acceptable"
    ("de;q=0.5,es;q=0.5", "de"),         # equal quality: the header's own order wins
    ("ja;q=1.0,de;q=0.5", "de"),         # an unsupported best still lets a supported one win
    ("de;level=1", "de"),                # an unknown parameter is ignored
    ("de-DE ; q=0.9", "de"),             # spaces around the parameter
    # nothing usable
    ("ja", None),                        # supported by no catalog
    ("sv", None),
    ("*", None),                         # the wildcard names no language
    (";;;=,", None),
    ("de;q=nonsense", None),             # a malformed weight is not a match
    ("", None),
    (None, None),
    ("x" * 400, None),
    ("de\nX-Injected: 1", None),         # a control character is never a language
])
def test_the_browser_tag_parser_reads_what_a_browser_sends(header, expected):
    assert language_step.browser_language(header, SUPPORTED) == expected


def test_the_parser_only_ever_answers_a_supported_language():
    """A hostile header can select a catalog, never a value the console cannot render."""
    for header in ("../../etc/passwd", "<script>", "zz", "de-CH-1996", "\x00de"):
        answer = language_step.browser_language(header, SUPPORTED)
        assert answer is None or answer in SUPPORTED


def test_no_header_is_explicitly_no_answer():
    """The no-header case is None (the caller falls back), not the base language."""
    assert language_step.browser_language(None, SUPPORTED) is None
    assert language_step.browser_language("", SUPPORTED) is None


def test_the_alias_table_only_names_languages_with_a_catalog():
    from services.webservice import i18n
    for tag, code in language_step.BROWSER_TAG_CODES.items():
        assert code in i18n.selectable_languages(), tag


# ------------------------------------------------------------- the prefill through the real app

@pytest.mark.parametrize("header, expected", [
    ("de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7", "de"),
    ("de", "de"),
    ("de-AT", "de"),
    ("de-CH,de;q=0.9", "de"),
    ("ja", "en"),
    ("zh-CN,zh;q=0.9", "cn"),
    ("zh-TW,zh-Hant;q=0.9", "cn"),
    ("cn", "cn"),
    ("sv", "en"),
    ("de;q=0.0,en;q=0.5", "en"),
    (";;;=,", "en"),
    ("*", "en"),
    ("", "en"),
])
@pytest.mark.asyncio
async def test_step_one_preselects_and_renders_the_browsers_language(tmp_path, header, expected):
    """The reported case and its neighbours: for each header, the SAME language is marked and drawn."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        headers = {"Accept-Language": header} if header else {}
        assert (await client.get(SETUP_PATH, params={"code": CODE}, headers=headers)).status_code == 303
        html = (await client.get(SETUP_PATH, headers=headers)).text
    assert marked(html) == expected
    assert rendered(html) == expected
    assert (GERMAN_TITLE in html) is (expected == "de")


@pytest.mark.asyncio
async def test_the_reported_case_a_german_browser_prefills_german(tmp_path):
    """No cookie and no draft — the browser decides, and it is honoured."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        headers = {"Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"}
        assert (await client.get(SETUP_PATH, params={"code": CODE}, headers=headers)).status_code == 303
        html = (await client.get(SETUP_PATH, headers=headers)).text
    assert marked(html) == "de"
    assert rendered(html) == "de"
    assert GERMAN_TITLE in html


@pytest.mark.asyncio
async def test_the_saved_draft_beats_the_browser(tmp_path):
    """The operator's own answer to this step wins over the browser, even with no cookie."""
    SetupDraft(step=2, language="en").save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": "de-DE,de;q=0.9"})).text
    assert marked(html) == "en" and rendered(html) == "en"


@pytest.mark.asyncio
async def test_the_saved_draft_beats_the_browser_the_other_way_round(tmp_path):
    SetupDraft(step=2, language="de").save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": "en-US,en;q=0.9"})).text
    assert marked(html) == "de" and rendered(html) == "de"


@pytest.mark.asyncio
async def test_the_explicit_switch_beats_both_the_draft_and_the_browser(tmp_path):
    """The flag switch (``/language?lang=``) is the explicit parameter: it outranks both."""
    SetupDraft(step=2, language="de").save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        switched = await client.get(LANGUAGE_PATH, params={"lang": "en", "next": f"{STEP_PATH}?n=1"},
                                    headers={"Accept-Language": "de-DE,de;q=0.9"})
        assert switched.status_code == 303
        assert "dcssb_lang=en" in switched.headers.get("set-cookie", "")
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": "de-DE,de;q=0.9"})).text
    assert marked(html) == "en" and rendered(html) == "en"


@pytest.mark.asyncio
async def test_a_language_parameter_on_the_step_beats_the_cookie_and_the_browser(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": STEP_PATH})
        html = (await client.get(STEP_PATH, params={"n": "1", "lang": "cn"},
                                 headers={"Accept-Language": "en-US,en;q=0.9"})).text
    assert marked(html) == "cn" and rendered(html) == "cn"


@pytest.mark.asyncio
async def test_an_unknown_explicit_value_is_skipped_not_echoed(tmp_path):
    """A crafted ``lang`` names no catalog: the next source answers, and nothing is reflected."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1", "lang": "../../etc/passwd"},
                                 headers={"Accept-Language": "de-DE,de;q=0.9"})).text
    assert "../../etc/passwd" not in html
    assert marked(html) == "de" and rendered(html) == "de"


@pytest.mark.asyncio
async def test_the_step_one_answer_is_recorded_as_the_prefilled_language(tmp_path):
    """Submitting the step records exactly the language step 1 offered and marks."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        html = (await enter(client)).text
        assert marked(html) == "en"
        response = await client.post(STEP_PATH, data={"_csrf_token": _token(html), "n": "1"},
                                     headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert response.status_code == 303
    assert "dcssb_lang=cn" in response.headers.get("set-cookie", "")
    assert SetupDraft.load(tmp_path).language == "cn"


def _token(html: str) -> str:
    found = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


@pytest.mark.asyncio
async def test_the_explicit_override_is_one_shot_and_is_not_pinned_into_the_switch(tmp_path):
    """``?lang=`` decides THIS render only; the flag links must not carry it back into the switch."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1", "lang": "cn"},
                                 headers={"Accept-Language": "en-US,en;q=0.9"})).text
        assert marked(html) == "cn"
        # a one-shot override is not persisted
        assert client.cookies.get("dcssb_lang") is None
        # ... and the switch a flag performs still works from that page
        switched = await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=1"})
        assert switched.status_code == 303
        after = (await client.get(STEP_PATH, params={"n": "1"},
                                  headers={"Accept-Language": "en-US,en;q=0.9"})).text
    assert marked(after) == "de"
    targets = re.findall(r'next=([^"]+)', html)
    assert targets and all("lang" not in target for target in targets)


# --------------------------------------------- the sentence naming where the preselection came from

def test_the_source_is_reported_for_every_branch_of_the_precedence():
    """The reported source is the branch that ACTUALLY answered, one row per rung of the ladder."""
    rows = [
        # lang parameter + cookie + draft + browser all present -> the parameter wins
        ({"lang": "cn", "cookie": "de", "header": GERMAN_HEADER, "draft": "en"},
         "cn", language_step.SOURCE_PARAMETER),
        ({"cookie": "de", "header": GERMAN_HEADER, "draft": "en"},
         "de", language_step.SOURCE_COOKIE),
        ({"header": GERMAN_HEADER, "draft": "en"},
         "en", language_step.SOURCE_ANSWER),
        ({"header": "de-DE,de;q=0.9"},
         "de", language_step.SOURCE_BROWSER),
        ({}, "en", language_step.SOURCE_DEFAULT),
        ({"default": "ru"}, "ru", language_step.SOURCE_DEFAULT),
    ]
    for kwargs, code, source in rows:
        draft = SetupDraft(step=2, language=kwargs.pop("draft")) if "draft" in kwargs else None
        choice = language_step.prefill_choice(_StubRequest(**kwargs), draft)
        assert (choice.code, choice.source) == (code, source), kwargs


def test_a_skipped_source_is_never_named():
    """A source that names no catalog is not the answer AND is not reported as the one that was."""
    draft = SetupDraft(step=2, language="ja")                 # no Japanese catalog
    choice = language_step.prefill_choice(
        _StubRequest(lang="../../etc/passwd", cookie="xx", header=GERMAN_HEADER), draft)
    assert (choice.code, choice.source) == ("de", language_step.SOURCE_BROWSER)
    assert choice.header == GERMAN_HEADER


def test_only_the_browser_source_carries_a_header():
    """The header is quoted back only when the header is what answered; every other source has none."""
    for kwargs, code in [({"lang": "cn"}, "cn"), ({"cookie": "de"}, "de"),
                         ({"default": "ru"}, "ru")]:
        choice = language_step.prefill_choice(_StubRequest(**kwargs), None)
        assert choice.code == code and choice.header is None, kwargs
    saved = language_step.prefill_choice(_StubRequest(header=GERMAN_HEADER),
                                         SetupDraft(step=2, language="en"))
    assert saved.source == language_step.SOURCE_ANSWER and saved.header is None


@pytest.mark.parametrize("value, expected", [
    ("de-DE,de;q=0.9", "de-DE,de;q=0.9"),
    ("de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7", "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("  de\n  X-Injected:\t1 ", "de X-Injected: 1"),          # whitespace collapsed, one line
    ("de\u0000", "de\u0000"),                                 # only whitespace is touched
    ("", ""),
    (None, ""),
    ("x" * 400, "x" * language_step.HEADER_DISPLAY_LIMIT + "..."),   # capped, never the page
])
def test_the_header_shown_back_is_collapsed_and_capped(value, expected):
    assert language_step.display_header(value) == expected


@pytest.mark.asyncio
async def test_the_reported_case_now_states_that_the_saved_answer_outranked_the_browser(tmp_path):
    """A German browser with an English saved answer: English is still marked, and now SAID out loud."""
    SetupDraft(step=2, language="en").save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": GERMAN_HEADER})).text
    assert marked(html) == "en" and rendered(html) == "en"
    assert source_line(html) == SAYS_ANSWER


@pytest.mark.asyncio
async def test_the_browser_sentence_quotes_the_header_it_read(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": GERMAN_HEADER})).text
    assert marked(html) == "de"
    # the page is German (the browser answered), so the sentence is the German one
    assert source_line(html) == says("de", language_step.SOURCE_BROWSER, header=GERMAN_HEADER)


@pytest.mark.asyncio
async def test_the_parameter_sentence_names_the_parameter(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1", "lang": "cn"},
                                 headers={"Accept-Language": GERMAN_HEADER})).text
    assert marked(html) == "cn"
    assert source_line(html) == says("cn", language_step.SOURCE_PARAMETER, param="lang")


@pytest.mark.asyncio
async def test_the_cookie_sentence_names_the_cookie(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "cn", "next": f"{STEP_PATH}?n=1"})
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": GERMAN_HEADER})).text
    assert marked(html) == "cn"
    assert source_line(html) == says("cn", language_step.SOURCE_COOKIE, cookie=i18n.COOKIE_NAME)


@pytest.mark.asyncio
async def test_the_default_sentence_says_nothing_else_named_one(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"})).text
    assert marked(html) == "en"
    assert source_line(html) == SAYS_DEFAULT


@pytest.mark.asyncio
async def test_the_sentence_is_rendered_in_the_readers_language(tmp_path):
    """The German page says it in German — the sentence is translated like the rest of the step."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "1"},
                                 headers={"Accept-Language": GERMAN_HEADER})).text
    assert GERMAN_TITLE in html
    assert source_line(html) == (f'Vorgewählt aus Deinem Browser — er sendet "{GERMAN_HEADER}".')


@pytest.mark.asyncio
async def test_only_the_language_step_says_where_the_preselection_came_from(tmp_path):
    """One line on step 1 — no later step repeats it."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        step_one = (await client.get(STEP_PATH, params={"n": "1"})).text
        assert SOURCE_RE.search(step_one)
        await client.post(STEP_PATH, data={"_csrf_token": _token(step_one), "n": "1"})
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
    assert not SOURCE_RE.search(step_two)


@pytest.mark.asyncio
async def test_changing_the_language_clears_the_stale_saved_answer(tmp_path):
    """Frank's fix: picking a language rewrites the saved answer, so the next page cannot revert."""
    SetupDraft(step=2, language="en").save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        before = (await client.get(STEP_PATH, params={"n": "1"},
                                   headers={"Accept-Language": GERMAN_HEADER})).text
        assert source_line(before) == SAYS_ANSWER
        switched = await client.get(LANGUAGE_PATH,
                                    params={"lang": "de", "next": f"{STEP_PATH}?n=1"},
                                    headers={"Accept-Language": GERMAN_HEADER})
        assert switched.status_code == 303
        assert "dcssb_lang=de" in switched.headers.get("set-cookie", "")
        after = (await client.get(STEP_PATH, params={"n": "1"},
                                  headers={"Accept-Language": GERMAN_HEADER})).text
        assert marked(after) == "de"
        assert source_line(after) == says("de", language_step.SOURCE_COOKIE,
                                         cookie=i18n.COOKIE_NAME)
        # with the console cookie gone, the SAVED answer is the one that answers — and it is German
        client.cookies.delete(i18n.COOKIE_NAME)
        without_cookie = (await client.get(STEP_PATH, params={"n": "1"},
                                           headers={"Accept-Language": GERMAN_HEADER})).text
        assert marked(without_cookie) == "de"
        assert source_line(without_cookie) == says("de", language_step.SOURCE_ANSWER)
        assert SetupDraft.load(tmp_path).language == "de"
        # and continuing keeps it: the NEXT step renders in German too
        response = await client.post(STEP_PATH, data={"_csrf_token": _token(after), "n": "1"},
                                     headers={"Accept-Language": GERMAN_HEADER})
        assert response.status_code == 303
        step_two = (await client.get(STEP_PATH, params={"n": "2"},
                                     headers={"Accept-Language": GERMAN_HEADER})).text
    assert rendered(step_two) == "de"
    assert SetupDraft.load(tmp_path).language == "de"


@pytest.mark.asyncio
async def test_every_source_survives_the_step_it_prefilled(tmp_path):
    """Change-then-continue for each source: the NEXT step (2) keeps the language the switch stored."""
    async def run(*, draft_language=None, header=None, default=None):
        if draft_language:
            SetupDraft(step=2, language=draft_language).save(tmp_path)
        app, _ = build(tmp_path)
        app.state.webui_default_language = default
        headers = {"Accept-Language": header} if header else {}
        async with asgi_client(app) as client:
            await enter(client)
            before = (await client.get(STEP_PATH, params={"n": "1"}, headers=headers)).text
            await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=1"},
                             headers=headers)
            changed = (await client.get(STEP_PATH, params={"n": "1"}, headers=headers)).text
            await client.post(STEP_PATH, data={"_csrf_token": _token(changed), "n": "1"},
                              headers=headers)
            next_step = (await client.get(STEP_PATH, params={"n": "2"}, headers=headers)).text
        draft_path(tmp_path).unlink(missing_ok=True)
        return before, changed, next_step

    before, changed, next_step = await run(header=GERMAN_HEADER)           # the browser answered
    assert marked(before) == "de"
    assert source_line(before) == says("de", language_step.SOURCE_BROWSER, header=GERMAN_HEADER)
    assert source_line(changed) == says("de", language_step.SOURCE_COOKIE,
                                       cookie=i18n.COOKIE_NAME)
    assert rendered(next_step) == "de"

    before, changed, next_step = await run(draft_language="en", header=GERMAN_HEADER)
    assert marked(before) == "en" and source_line(before) == SAYS_ANSWER
    assert marked(changed) == "de"
    assert source_line(changed) == says("de", language_step.SOURCE_COOKIE,
                                        cookie=i18n.COOKIE_NAME)
    assert rendered(next_step) == "de"

    before, changed, next_step = await run(default="ru")                  # the install default did
    assert marked(before) == "ru"
    assert source_line(before) == says("ru", language_step.SOURCE_DEFAULT)
    assert rendered(next_step) == "de"


def test_the_english_catalog_carries_the_sentences_the_step_shows():
    """The msgid text and its holes are pinned here — English is the source every catalog starts from."""
    assert says("en", language_step.SOURCE_BROWSER, header=GERMAN_HEADER) == (
        f'Preselected from your browser — it sent "{GERMAN_HEADER}".')
    assert says("en", language_step.SOURCE_PARAMETER, param="lang") == (
        'Preselected from the language in the link you followed — the "lang" parameter.')
    assert says("en", language_step.SOURCE_COOKIE, cookie=i18n.COOKIE_NAME) == (
        f'Preselected from the language this console stored for you — the '
        f'"{i18n.COOKIE_NAME}" cookie.')
    assert says("en", language_step.SOURCE_ANSWER) == SAYS_ANSWER
    assert says("en", language_step.SOURCE_DEFAULT) == SAYS_DEFAULT
    # every source the resolver can report has its own sentence — a new one cannot render nothing
    assert set(T_LANG_SOURCE) == {language_step.SOURCE_PARAMETER, language_step.SOURCE_COOKIE,
                                  language_step.SOURCE_ANSWER, language_step.SOURCE_BROWSER,
                                  language_step.SOURCE_DEFAULT}


@pytest.mark.asyncio
async def test_the_language_switch_creates_no_draft_of_its_own(tmp_path):
    """Before step 1 has an answer there is nothing stale to clear — and nothing is invented."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        switched = await client.get(LANGUAGE_PATH,
                                    params={"lang": "de", "next": f"{STEP_PATH}?n=1"})
        assert switched.status_code == 303
        assert (await client.get(STEP_PATH, params={"n": "1"})).status_code == 200
    assert not draft_path(tmp_path).exists(), "the language switch wrote an answer nobody gave"
