"""The Language step's prefill: the language the tour offers before the operator has chosen one.

WHAT THIS IS. Step 1's answer IS the console's language — its flag row is the control, and the code
it marks is the preselected answer. This module computes that code for a request, so the tour renders
in the same language it offers and the two can never disagree.

THE SOURCES, highest precedence first:

1. an explicit language parameter — a validated ``lang`` query parameter on the request, which is the
   form the tour's own switch carries (``/language?lang=de``), or the ``dcssb_lang`` cookie that the
   same switch persists;
2. the setup draft's ``language`` — the operator's saved answer to this step, kept on disk so it
   survives a cookie-less browser or a cleared cookie jar;
3. the browser's ``Accept-Language`` header;
4. the install-wide default (``main.yaml``'s ``language:``, recorded on the application), else
   English.

Every source is validated against the languages that have a ``webui`` catalog
(:func:`services.webservice.i18n.selectable_languages`); a value naming no catalog is skipped, never
echoed.

WHICH SOURCE ANSWERED IS REPORTED, NOT GUESSED. :func:`prefill_choice` returns the code together with
the source it came from (:data:`SOURCE_PARAMETER`, :data:`SOURCE_COOKIE`, :data:`SOURCE_ANSWER`,
:data:`SOURCE_BROWSER`, :data:`SOURCE_DEFAULT`) and, for the browser, the header it read — the tour
prints that as the one sentence saying where the preselection came from. The decision is taken once,
in one place: :func:`prefill_language` is the same function's code alone, so the answer and its
account of itself can never disagree.

THE BROWSER TAG MAPPING. A browser names languages with tags this project does not use verbatim: it
sends ``de-AT`` where the catalog is ``de``, and it sends ``zh``/``zh-CN``/``zh-Hant`` where the
project's Chinese catalog is **``cn``** (``locale/cn/LC_MESSAGES/``, and the bot's own code for
Chinese). :data:`BROWSER_TAG_CODES` is the explicit list of such tags; every other tag falls back to
its own primary subtag, which is the catalog's code for the rest of the set (``en``/``de``/``es``/
``ru``).

The header is parsed per RFC 7231: comma-separated tags, each with optional ``;q=`` weight. The
highest non-zero weight whose language is supported wins; ties keep the header's own order. A token
with no usable code (an empty token, a ``*`` wildcard, a malformed one) is skipped, and a header that
is absent, empty, or wholly unsupported yields ``None`` — the tour then falls back to the install
default, never to an invented language.
"""
from __future__ import annotations

__all__ = ["BROWSER_TAG_CODES", "HEADER_DISPLAY_LIMIT", "SOURCE_ANSWER", "SOURCE_BROWSER",
           "SOURCE_COOKIE", "SOURCE_DEFAULT", "SOURCE_PARAMETER", "LanguageChoice",
           "browser_language", "display_header", "prefill_choice", "prefill_language"]

from dataclasses import dataclass

from .. import i18n

#: Where a preselected language came from. One constant per source in the module's precedence, so the
#: step's sentence names the branch that actually answered.
SOURCE_PARAMETER = "parameter"   #: the ``lang`` parameter on this request
SOURCE_COOKIE = "cookie"         #: the console's own ``dcssb_lang`` cookie
SOURCE_ANSWER = "answer"         #: the setup draft's ``language`` — the operator's saved answer
SOURCE_BROWSER = "browser"       #: the ``Accept-Language`` header
SOURCE_DEFAULT = "default"       #: the install-wide default, else English

#: The most of an ``Accept-Language`` value the step prints back when the browser answered. A real
#: header is far shorter; the cap is only so a hostile, enormous one cannot become the page.
HEADER_DISPLAY_LIMIT = 80

#: Browser tags whose PRIMARY subtag is not this project's catalog code. Chinese is the one such
#: family here: browsers say ``zh`` (with region/script variants), the project's catalog is ``cn``.
#: Regional tags that keep their primary subtag (``de-AT``/``de-CH`` -> ``de``) need no entry.
BROWSER_TAG_CODES: dict[str, str] = {
    "zh": "cn",
    "zh-cn": "cn",
    "zh-sg": "cn",
    "zh-my": "cn",
    "zh-hans": "cn",
    "zh-hans-cn": "cn",
    "zh-hans-sg": "cn",
    "zh-tw": "cn",
    "zh-hk": "cn",
    "zh-mo": "cn",
    "zh-hant": "cn",
    "zh-hant-tw": "cn",
    "zh-hant-hk": "cn",
}


def _tag_code(tag: str) -> str | None:
    """A browser language tag as a candidate project code, or ``None`` when it names no language.

    The tag is case-folded and ``_``/``-`` are treated alike. An exact tag from
    :data:`BROWSER_TAG_CODES` wins; otherwise the primary subtag is the candidate (so ``de-AT`` ->
    ``de``). The caller still checks membership in the catalog set.
    """
    head = str(tag or "").strip().lower().replace("_", "-")
    if not head:
        return None
    mapped = BROWSER_TAG_CODES.get(head)
    if mapped:
        return mapped
    return head.split("-", 1)[0]


def browser_language(header, supported: tuple[str, ...]) -> str | None:
    """The best supported language in an ``Accept-Language`` header, or ``None``.

    Quality values are honoured; at equal quality the header's own order decides. Every token is
    validated against *supported*, so a hostile header can only ever select a language that exists.
    """
    if not header:
        return None
    best: tuple[float, int] | None = None
    best_code: str | None = None
    for order, token in enumerate(str(header).split(",")):
        parts = token.split(";")
        code = _tag_code(parts[0])
        if code is None:
            continue
        quality = 1.0
        for param in parts[1:]:
            name, _, raw = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(raw.strip())
                except ValueError:
                    quality = 0.0
        if quality <= 0 or code not in supported:
            continue
        rank = (quality, -order)
        if best is None or rank > best:
            best, best_code = rank, code
    return best_code


@dataclass(frozen=True)
class LanguageChoice:
    """The language the tour preselects for a request, and where it came from.

    ``header`` is the ``Accept-Language`` value the browser sent when ``source`` is
    :data:`SOURCE_BROWSER` — the step prints it back so the claim can be checked at a glance — and
    ``None`` for every other source.
    """
    code: str
    source: str
    header: str | None = None


def display_header(value) -> str:
    """An ``Accept-Language`` value as the step shows it back: whitespace collapsed, capped.

    Display only — the parser reads the raw header, never this form.
    """
    text = " ".join(str(value or "").split())
    if len(text) > HEADER_DISPLAY_LIMIT:
        return text[:HEADER_DISPLAY_LIMIT] + "..."
    return text


def _query_of(request) -> str | None:
    params = getattr(request, "query_params", None)
    if params is None:
        return None
    try:
        return params.get("lang")
    except AttributeError:  # a plain dict in a test stub
        return params.get("lang") if isinstance(params, dict) else None


def _cookie_of(request) -> str | None:
    cookies = getattr(request, "cookies", None)
    return cookies.get(i18n.COOKIE_NAME) if cookies is not None else None


def _header_of(request) -> str | None:
    headers = getattr(request, "headers", None)
    return headers.get("accept-language") if headers is not None else None


def _default_of(request) -> str | None:
    state = getattr(getattr(request, "app", None), "state", None)
    return getattr(state, "webui_default_language", None)


def prefill_choice(request, draft, supported: tuple[str, ...] | None = None) -> LanguageChoice:
    """The language to preselect (and render) for *request*, with the source that answered.

    *draft* is the setup draft; its ``language`` is the operator's saved answer. *supported* defaults
    to the catalogs that exist, so a caller never has to re-derive them. The precedence and the
    per-source validation are the module's; the source is reported only after a value has been
    accepted, so a skipped source is never named.
    """
    languages = supported if supported is not None else i18n.selectable_languages()
    explicit = i18n.normalise_language(_query_of(request))
    if explicit:
        return LanguageChoice(explicit, SOURCE_PARAMETER)
    cookie = i18n.normalise_language(_cookie_of(request))
    if cookie:
        return LanguageChoice(cookie, SOURCE_COOKIE)
    saved = i18n.normalise_language(getattr(draft, "language", None) if draft is not None else None)
    if saved:
        return LanguageChoice(saved, SOURCE_ANSWER)
    header = _header_of(request)
    browser = browser_language(header, languages)
    if browser:
        return LanguageChoice(browser, SOURCE_BROWSER, display_header(header))
    return LanguageChoice(i18n.normalise_language(_default_of(request)) or i18n.BASE_LANGUAGE,
                          SOURCE_DEFAULT)


def prefill_language(request, draft, supported: tuple[str, ...] | None = None) -> str:
    """The language to preselect (and render) for *request* — :func:`prefill_choice`'s code alone.

    *draft* is the setup draft; its ``language`` is the operator's saved answer. *supported* defaults
    to the catalogs that exist, so a caller never has to re-derive them.
    """
    return prefill_choice(request, draft, supported).code
