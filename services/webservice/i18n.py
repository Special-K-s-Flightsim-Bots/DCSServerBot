"""The admin web UI in several languages: the catalogs, the per-request translator, and the
language control.

WHAT THIS IS. The console is translated with **Babel + gettext, driven by Jinja's own i18n
extension**. Strings are wrapped as ``{% trans %}…{% endtrans %}`` in templates and ``_()`` in the
console's Python; Babel extracts them straight out of both (``babel.cfg`` + ``pybabel extract``), so
there is no hand-kept list of translatable strings to fall out of date. The catalogs live in the
bot's own tree, ``locale/<lang>/LC_MESSAGES/``, under the **``webui`` domain** — the same workflow a
translator already knows from the bot's plugins, but a domain of its own, because Discord and the
frontend share almost nothing.

ENGLISH IS THE MSGID, AND THAT IS THE POINT. ``en`` is the source language: wrapping a string is a
no-op until a translation exists, so the tree stays green and nothing changes visually while the
migration proceeds slice by slice. ``locale/en/LC_MESSAGES/webui.po`` is the identity catalog (its
msgstrs are their msgids) — it exists so English is a first-class choice in the language control and
so a new translator has a template to copy, not because anything needs translating.

A RUNTIME WITH NO NEW DEPENDENCY. The console uses the standard library's :mod:`gettext` and Jinja's
i18n extension. Babel is a **build/developer** tool only (like the ``msgfmt`` step the bot's own
docs describe): it extracts and compiles the catalogs, it never runs in the request path.

THE LANGUAGE OF A REQUEST, highest precedence first:

1. the visitor's **stored choice** — the ``dcssb_lang`` cookie, set by :data:`LANGUAGE_PATH`;
2. the browser's ``Accept-Language`` header;
3. the ``language:`` key in ``main.yaml`` — today's install-wide setting, and the default when
   nothing else applies.

THE STORE, AND WHY IT CANNOT NAME AN UNKNOWN LANGUAGE. The stored choice is a **dedicated cookie**,
not the authenticated session — for two reasons. It has to work **before login** (the login page is
where a visitor first wants it), and the login and logout flows call ``request.session.clear()``
(session-fixation defence), which would wipe a preference kept in the session on every sign-in. One
mechanism, one fact: nothing else stores the language. Every value read from the cookie — or from
the header, or from ``main.yaml`` — is run through :func:`normalise_language`, which returns a code
ONLY when it is one of the languages that actually have a ``webui`` catalog
(:func:`selectable_languages`). An unknown, malformed, over-long or control-character-bearing value
is not an error and is never echoed: it simply yields ``None`` and the next source in the precedence
is consulted. A visitor therefore cannot make the console look for ``../../etc/passwd`` or a
language that does not exist.

THE CONTROL SHOWS FLAGS, NOT NAMES. With more languages coming, a row of names is too long, so each
option is a small SVG flag (a neutral globe for English, the source language) with the language's own
endonym kept as its ``title``/``aria-label`` — the name is not drawn, but hovering and a screen reader
still say which language it is. Flag EMOJI are deliberately NOT used (Windows renders them as the two
regional-indicator letters, so the control would look broken on the maintainer's own machine); the
assets are vendored SVG files (see ``static/flags/``). Only languages with a compiled ``webui``
catalog are offered, and the control stays plain links, so it works with JavaScript off.

NO LEAK BETWEEN REQUESTS. The console runs **inside the bot's own process**, so a global
``install_gettext_translations``/``set_language`` would bleed the console's language into the bot's
Discord strings (and back). Instead the language is resolved **per request** and applied through a
**Jinja environment per language** (:func:`environment_for_language`), built lazily and cached on the
registrar. Each environment carries its own immutable translations object; no render mutates shared
state, so a render in German cannot change what the next render in English sees (proved in
``tests/test_webui_i18n.py``).
"""
from __future__ import annotations

import gettext
import logging
import os
import unicodedata
import weakref

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import permissions, templating

__all__ = [
    "LOCALE_DIR", "DOMAIN", "BASE_LANGUAGE", "COOKIE_NAME", "COOKIE_MAX_AGE", "LANGUAGE_PATH",
    "MESSAGES_PATH", "JS_MESSAGES",
    "LANGUAGE_NAMES", "LANGUAGE_FLAGS", "FLAG_ASSET_PREFIX", "FLAG_NEUTRAL_ASSET", "flag_asset",
    "catalog_languages", "selectable_languages", "display_name",
    "normalise_language", "default_language", "resolve_language", "translations_for",
    "environment_for_language", "environment_for", "language_control", "render", "render_fragment",
    "messages_for", "messages_for_language", "messages_script", "messages_script_for",
    "messages_capability",
    "languages_from_main_yaml", "capabilities", "add_routes", "_",
]

log = logging.getLogger(__name__)

#: the bot's own locale tree (``locale/<lang>/LC_MESSAGES/<domain>.mo``), addressed absolutely from
#: this file so the console never depends on the process's working directory.
LOCALE_DIR = Path(__file__).resolve().parents[2] / "locale"

#: the console's OWN catalog domain — separate strings from the bot's, one translator workflow.
DOMAIN = "webui"

#: the source language: English stays the msgid, so a wrapped-but-untranslated string is a no-op.
BASE_LANGUAGE = "en"

#: the cookie that stores the visitor's choice. Its own cookie on purpose — see the module docstring.
COOKIE_NAME = "dcssb_lang"

#: how long the choice is remembered (one year; a preference, not a credential).
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

#: the path the language control links to (absolute and constant, like every shell path).
LANGUAGE_PATH = "/language"

#: THE SERVED MESSAGE MAP. The console's static JavaScript carries no user-visible string of its own;
#: it is served the strings for the REQUEST's language here (``/i18n/messages.js``), and only swaps
#: them. This is a served script, not a static asset, because a static file cannot be language-shaped
#: — and it is a GATED route (see :func:`capabilities`), so it respects the console's access gate
#: exactly like every page and asset.
MESSAGES_PATH = "/i18n/messages.js"

#: the display names the control shows. A language's OWN endonym, so the options are readable to
#: somebody who does not read the current language. A catalog with no entry here still appears in
#: the control, labelled by its code — a new catalog is never hidden for want of a name.
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "de": "Deutsch", "es": "Español", "ru": "Русский",
                                 "cn": "中文"}

#: the static area the flag SVGs are served from (the shell's own asset path, served by the same
#: gated frontend every other asset uses; spelled here rather than imported from ``shell`` because
#: ``shell`` imports THIS module, so a module-level import back would be a cycle).
FLAG_ASSET_PREFIX = "/static/flags"

#: the NEUTRAL marker for the source language: a globe, never a national flag. English is the msgid
#: — the way back from any translation — and a globe sidesteps the GB/US question entirely. It is
#: also the fallback for any catalog that has no flag asset yet, so a new language is never hidden.
FLAG_NEUTRAL_ASSET = "globe.svg"

#: language code -> its SVG flag asset (a filename under :data:`FLAG_ASSET_PREFIX`). VENDORED,
#: public-domain / MIT artwork only; see ``static/flags/SOURCE.txt`` beside the files. Windows does
#: not render flag EMOJI (it falls back to the two regional-indicator letters), which is exactly why
#: these are real SVG files and not emoji. A code absent here gets the neutral globe.
LANGUAGE_FLAGS: dict[str, str] = {"de": "de.svg", "es": "es.svg", "ru": "ru.svg", "cn": "cn.svg"}


def flag_asset(code: str) -> str:
    """The URL of *code*'s flag SVG: its own asset, or the neutral globe when it has none."""
    return f"{FLAG_ASSET_PREFIX}/{LANGUAGE_FLAGS.get(code, FLAG_NEUTRAL_ASSET)}"


def _(message: str) -> str:
    """Mark a Python string as translatable, and return it UNCHANGED.

    This is the extraction marker for the console's Python, in the same spirit as the bot's own
    ``_``: Babel's Python extractor recognises the call and records the literal, while at runtime it
    is an identity — no import-time translation, therefore no import-time language and no global
    state. The string is actually translated where it is RENDERED, by the template environment's own
    ``_`` (``{{ _(label) }}``), which is built per language. That split is what lets a nav label
    declared in a page module be translated without the module having to know the request.
    """
    return message


# ------------------------------------------------------------------- the catalogs that exist

#: The catalog list is CACHED, keyed by the ``locale/`` directory's own mtime. That is the one cheap
#: signature that changes exactly when the SET of languages changes: compiling a NEW language creates
#: a ``locale/<lang>/`` directory (updating ``locale/``'s mtime), and removing one deletes it. A
#: recompile of an EXISTING ``.mo`` writes inside ``locale/<lang>/LC_MESSAGES/`` and does NOT change
#: the language set, so a stale entry is still CORRECT — the answer is a list of codes, not of
#: translations. Chosen over a TTL because a recompiled catalog is picked up at once (the next call
#: after the directory changes), with no wait and no restart.
_CATALOG_CACHE: dict[str, tuple] = {}


def _locale_signature() -> tuple | None:
    """``locale/``'s mtime, or ``None`` when the directory does not exist."""
    try:
        return (os.stat(LOCALE_DIR).st_mtime_ns,)
    except OSError:
        return None


def catalog_languages() -> tuple[str, ...]:
    """Every language that actually has a compiled ``webui`` catalog, sorted.

    The test is the ``<lang>/LC_MESSAGES/webui.mo`` file the standard library reads — a language
    with only a ``.po`` (not yet compiled) is NOT usable at runtime and is not offered, which is the
    "never an empty language" rule: the control offers exactly what will render, nothing else.

    CACHED — see :data:`_CATALOG_CACHE` — and re-scanned when ``locale/``'s own mtime changes, so a
    newly compiled catalog is picked up without a restart.
    """
    signature = _locale_signature()
    if signature is None:
        return ()
    cached = _CATALOG_CACHE.get("value")
    if cached is not None and cached[0] == signature:
        return cached[1]
    found = tuple(child.name for child in sorted(LOCALE_DIR.iterdir())
                  if (child / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file())
    _CATALOG_CACHE["value"] = (signature, found)
    return found


def selectable_languages() -> tuple[str, ...]:
    """The languages a visitor may pick: the base (English) first, then the catalogs, sorted.

    English is always offered (it is the msgid, and the way back from any translation); the rest are
    the languages with a real ``webui`` catalog. This is the ONE list the control and the validator
    both read, so a language can never be offered that :func:`normalise_language` would reject.
    """
    others = [code for code in catalog_languages() if code != BASE_LANGUAGE]
    return (BASE_LANGUAGE, *others)


def display_name(code: str) -> str:
    """The endonym for *code*, or the code itself when no name is declared for it."""
    return LANGUAGE_NAMES.get(code, code)


def _base(code: str) -> str:
    """The base language of a tag: ``de-DE``/``de_DE``/``DE`` -> ``de``. The region is dropped.

    gettext catalogs are keyed by base language here (``locale/de/...``), so a browser's ``de-DE``
    has to be read as ``de``; the country is never part of the key.
    """
    head = str(code or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return head


def normalise_language(value) -> str | None:
    """*value* as a supported language code, or ``None``.

    THE ONE VALIDATOR, and the reason the stored choice "cannot name an unknown language": a value
    is accepted ONLY when its base matches a language in :func:`selectable_languages`. Everything
    else — an unknown code, a path traversal, a control character, an unbounded string — is refused
    by that membership test, not by a pattern. The length cap runs first so a hostile value cannot be
    capitalised on before it is compared.

    The code is NORMALISED: it is case-folded (an uppercase ``DE`` is accepted and read as ``de``),
    ``_`` and ``-`` separators are equivalent, and any region is dropped (``de-DE``/``de_DE`` -> ``de``).
    """
    if not isinstance(value, str) or not value:
        return None
    if len(value) > 32:
        return None
    if any(unicodedata.category(char).startswith("C") for char in value):
        return None
    code = _base(value)
    return code if code in selectable_languages() else None


def _accept_language(header, supported: tuple[str, ...]) -> str | None:
    """The best supported language in an ``Accept-Language`` header, or ``None``.

    Quality values are honoured (a ``;q=0.9`` reorders the list); the highest non-zero quality whose
    base language is supported wins. The header is attacker-shaped like any other, so every token
    goes through the same membership test the cookie does — a token that is not a supported language
    is simply skipped.
    """
    if not header:
        return None
    best_rank: tuple[float, int] | None = None
    best_code: str | None = None
    for order, token in enumerate(str(header).split(",")):
        parts = token.split(";")
        code = _base(parts[0])
        if not code:
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
        # a stable tie-break on header order keeps the browser's own preference intact at equal q
        rank = (quality, -order)
        if best_rank is None or rank > best_rank:
            best_rank, best_code = rank, code
    return best_code


def languages_from_main_yaml(config_dir) -> str | None:
    """The ``language:`` value in the install's ``main.yaml``, or ``None``.

    This is today's install-wide setting. It is read defensively: a missing file, a missing key or a
    value that names no ``webui`` catalog all yield ``None``, and the caller falls back to the base
    language. It never raises into the shell install.
    """
    path = Path(config_dir) / "main.yaml"
    try:
        from ruamel.yaml import YAML
        data = YAML().load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - any read/parse failure means "not configured"
        log.debug("Web UI i18n: no usable language in '%s'; the base language is the default.", path)
        return None
    return normalise_language(data.get("language"))


def default_language(config_dir) -> str:
    """The install-wide default language: ``main.yaml``'s, or English."""
    return languages_from_main_yaml(config_dir) or BASE_LANGUAGE


# ------------------------------------------------------------------- the request's language

def resolve_language(request) -> str:
    """The language THIS request renders in — the precedence, in ONE place.

    Stored choice -> ``Accept-Language`` -> ``main.yaml`` (installed once on the application as
    ``webui_default_language``). Every source is validated; a value that names no catalog is skipped
    rather than trusted, so the answer is always a real language.
    """
    stored = normalise_language(_cookie_of(request))
    if stored:
        return stored
    header = _accept_language(_header_of(request, "accept-language"), selectable_languages())
    if header:
        return header
    state = getattr(getattr(request, "app", None), "state", None)
    configured = normalise_language(getattr(state, "webui_default_language", None))
    return configured or BASE_LANGUAGE


def _cookie_of(request) -> str | None:
    cookies = getattr(request, "cookies", None)
    return cookies.get(COOKIE_NAME) if cookies is not None else None


def _header_of(request, name: str) -> str | None:
    headers = getattr(request, "headers", None)
    return headers.get(name) if headers is not None else None


# ------------------------------------------------------------------- the per-language translator

def translations_for(language: str) -> gettext.NullTranslations:
    """The gettext object for *language*: its ``webui`` catalog, or :class:`gettext.NullTranslations`.

    ``fallback=True`` means a missing catalog degrades to the msgid rather than raising — the same
    no-op-until-translated property, stated once.
    """
    return gettext.translation(DOMAIN, localedir=str(LOCALE_DIR), languages=[language],
                               fallback=True)


#: registrar -> {language: Environment}. A WeakKeyDictionary so a discarded application (and its
#: registrar) frees its environments; keyed by a REGISTRAR rather than by the module so one test's
#: environments can never answer another test's render.
_ENVIRONMENTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def environment_for_language(registrar, language: str):
    """The Jinja environment for *language*, built lazily and cached on *registrar*.

    This is the whole leak defence: each environment owns its translations object, so applying a
    language is a property of the environment and never of shared, mutable global state. The base
    language reuses the shell's own environment (which is built with NullTranslations and is
    therefore already English); any other language gets its own environment.
    """
    base = getattr(registrar, "environment", None)
    if language == BASE_LANGUAGE and base is not None:
        return base
    per_registrar = _ENVIRONMENTS.get(registrar)
    if per_registrar is None:
        per_registrar = {}
        _ENVIRONMENTS[registrar] = per_registrar
    environment = per_registrar.get(language)
    if environment is None:
        environment = templating.build_environment(registrar,
                                                   translations=translations_for(language))
        per_registrar[language] = environment
    return environment


def environment_for(request):
    """The Jinja environment THIS request renders with — the per-language one, or the shell's.

    Raises the console's own 503 when the shell is not installed, exactly as every page used to,
    so the failure shape is unchanged.
    """
    base = getattr(getattr(getattr(request, "app", None), "state", None),
                   "webui_templates", None)
    if base is None:  # pragma: no cover - installed by the shell
        raise HTTPException(status_code=503,
                            detail="The admin web UI templates are not installed.")
    registrar = getattr(request.app.state, "webui_registrar", None)
    if registrar is None:
        return base
    return environment_for_language(registrar, resolve_language(request))


def render(request, name: str, **context) -> str:
    """Render a FULL page in the request's language, with the language control in the context.

    Every page that renders the shell goes through here, which is what makes the sidebar and the
    brand follow the chosen language without each page knowing how. A caller may pass its own
    ``language`` (respecting it), otherwise the control is built from the request.
    """
    context.setdefault("language", language_control(request))
    return environment_for(request).get_template(name).render(**context)


def render_fragment(request, name: str, **context) -> str:
    """Render a fragment (no shell chrome) in the request's language — same environment, no control."""
    return environment_for(request).get_template(name).render(**context)


# ------------------------------------------------------------------- the language control

def language_control(request: Request) -> dict:
    """The language control as data: the current language, the options, and where a switch returns to.

    The options come from :func:`selectable_languages`, so the control can only ever offer a language
    that has a catalog. ``next`` is the current PATH ONLY — never the query string — for the same
    reason the login redirect carries only the path: the query is attacker-controlled and a value the
    visitor typed must never be reflected into a page. It is validated again by the route, never
    trusted from the client.
    """
    current = resolve_language(request)
    return {
        "current": current,
        "action": LANGUAGE_PATH,
        "next": _return_path(request),
        "languages": [{"code": code, "name": display_name(code), "flag": flag_asset(code),
                       "active": code == current}
                      for code in selectable_languages()],
    }


def _return_path(request) -> str:
    """Where a language switch returns to: a page the console DECLARES, else the site root.

    The value is the CURRENT path, but only when that path is one of the console's own page paths
    (``registrar.page_paths``) or the sign-in page. That is deliberately narrower than "the path the
    request carried": a raw path is still a reflection, and the login page must not advertise a door
    by echoing it (a disabled ``/auth/discord`` is exactly such a path — see the Discord error-page
    tests). A path the console does not declare therefore resolves to ``/``, which is a page it does.
    The switch route re-validates the value (``_safe_target``) and never trusts the client's copy.
    """
    url = getattr(request, "url", None)
    path = (getattr(url, "path", None) or "/") if url is not None else "/"
    registrar = getattr(getattr(getattr(request, "app", None), "state", None),
                        "webui_registrar", None)
    # imported LAZILY: ``auth.routes`` imports this module at top level, so a module-level import
    # here would be a cycle. At request time both modules are loaded.
    from .auth import routes as auth_routes
    allowed: set[str] = set(getattr(registrar, "page_paths", ()) or ())
    allowed.add(auth_routes.LOGIN_PATH)
    return path if path in allowed else "/"


# ------------------------------------------------------------------- the served message map
#
# THE MECHANISM (established by this slice, documented in I18N.md): the console's static
# JavaScript carries NO user-visible string and NO translation logic. The strings are served to it,
# per language, on a gated route (``/i18n/messages.js``) — the server renders the map, the JS only
# looks a key up and swaps a value in. This is deliberately NOT a second translation system: the
# values are the SAME ``webui`` catalog the pages use, resolved for the same request through the same
# precedence, so a page and its scripts can never disagree about the language.

#: key -> the English msgid, one per user-visible string the console's JavaScript swaps. The values
#: are ``_()``-marked so Babel extracts them into the ``webui`` catalog (the German draft carries a
#: msgstr for each). A ``{name}`` hole is filled in the BROWSER by ``dcssbMsg`` below from a value the
#: server already knew (a count, a retry interval) — the JS composes nothing and translates nothing.
JS_MESSAGES: dict[str, str] = {
    # the console's live indicator (static/shell.js, on the dashboard and the Logs page)
    "live.status": _("Live updates: {status}"),
    "live.on": _("on"),
    "live.connecting": _("connecting…"),
    "live.polling": _("polling every 10 seconds"),
    "live.not_ready": _("the bot is not ready yet — retrying in {seconds}s"),
    "live.not_permitted": _("not permitted — sign in again"),
    "live.sign_in_again": _("Sign in again"),
    # the server log follower (static/server-log.js, on the DCS Log / Events tabs)
    "log.load_older": _("Load older"),
    "log.not_available": _("not available"),
    "log.no_file": _("The log file is not there yet."),
    "log.waiting": _("waiting for the log file to appear"),
    "log.retrying": _("retrying…"),
    "log.rotated_new": _("— the log was rotated; showing the new file —"),
    "log.rotated_while": _("— the log was rotated while loading older lines —"),
    "log.rotated": _("the log was rotated"),
    "log.restarted": _("Log restarted"),
    "log.start_of_log": _("Start of log"),
    "log.following": _("following while this tab is open"),
    "log.paused": _("paused — this tab is hidden"),
    # the missions selection bar (static/mission-select.js, on the Missions tab)
    "mission.count": _("{n} of {total} selected"),
    "mission.remove_selected": _("Remove selected…"),
    "mission.remove_one": _("Remove 1 mission…"),
    "mission.remove_many": _("Remove {n} missions…"),
    # the configuration password reveal (static/reveal.js, on the Configuration tab)
    "reveal.show": _("Show password"),
    "reveal.hide": _("Hide password"),
    "reveal.nothing": _("Nothing to show — type a password first, then this reveals what you typed."),
}

#: the ONE substitution helper the served script installs. It LOOKS A KEY UP and replaces ``{name}``
#: holes from the caller's params — it translates nothing (the value it is handed is already the
#: request's language) and carries no per-string logic, so adding a string is adding a key to
#: :data:`JS_MESSAGES`, never a JS edit.
_MESSAGES_HELPER = (
    "(function () {\n"
    "  \"use strict\";\n"
    "  var table = window.DCSSB_MESSAGES || {};\n"
    "  window.dcssbMsg = function (key, params) {\n"
    "    var text = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : key;\n"
    "    if (params) {\n"
    "      for (var name in params) {\n"
    "        if (Object.prototype.hasOwnProperty.call(params, name)) {\n"
    "          text = text.split(\"{\" + name + \"}\").join(params[name]);\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "    return text;\n"
    "  };\n"
    "})();\n"
)


def messages_for(request) -> dict[str, str]:
    """The message map for THIS request's language: key -> the translated string.

    Every value is looked up in the request's language through the SAME :func:`translations_for` the
    pages use, so a page and its scripts cannot render in different languages. English stays the
    msgid: with no translation (or the base language) the map is the English strings verbatim.
    """
    return messages_for_language(resolve_language(request))


def messages_for_language(language: str) -> dict[str, str]:
    """The message map for *language* — the request-free half (used by the tests' JS harness)."""
    translations = translations_for(language)
    return {key: translations.gettext(msgid) for key, msgid in JS_MESSAGES.items()}


def messages_script(request) -> str:
    """The served script for THIS request's language: the map plus the ONE substitution helper."""
    return messages_script_for(resolve_language(request))


def messages_script_for(language: str) -> str:
    """The served script for *language*: ``window.DCSSB_MESSAGES`` (JSON) + ``window.dcssbMsg``.

    ``base.html`` includes this BEFORE the scripts that read it, so ``window.DCSSB_MESSAGES`` and
    ``window.dcssbMsg`` exist by the time they run. Served, never cached across languages
    (``Cache-Control: no-store`` on the route): a shared cache replaying a German map to an English
    reader is exactly the bug this avoids.
    """
    import json
    payload = json.dumps(messages_for_language(language), ensure_ascii=False, sort_keys=True)
    return f"window.DCSSB_MESSAGES = {payload};\n{_MESSAGES_HELPER}"


def messages_capability() -> str:
    """The capability the message route declares: the console's own read capability.

    Reused, never re-spelled: ``dashboard.view`` is declared by ``pages/dashboard`` and is the role
    model every console read page shares (``Admin``, ``DCS Admin``, and a MANAGER via the scope
    grant) — the same capability the live stream (``pages/live``) is gated by, so the console's own
    scripts reach the map wherever they run.
    """
    from .pages import dashboard as dashboard_page
    dashboard_page.declare()
    return dashboard_page.DASHBOARD_CAPABILITY


# ------------------------------------------------------------------- the switch route

def _safe_target(value) -> str:
    """A local path (with query) to return to after a language switch — or ``/``.

    The SAME rule the login flow applies to its ``next`` (``services.webservice.auth.routes.
    _safe_next``): control characters are removed, and a value that is not a rooted local path (an
    absolute URL, a protocol-relative ``//host``, a backslash) is refused. The value comes from the
    page the visitor was on, so it is attacker-shaped and is validated, not trusted.
    """
    if not isinstance(value, str) or not value:
        return "/"
    cleaned = "".join(char for char in value
                      if not unicodedata.category(char).startswith("C"))
    if not cleaned.startswith("/") or cleaned.startswith("//") or "\\" in cleaned:
        return "/"
    return cleaned


def capabilities() -> dict[str, str]:
    """The switch route is PUBLIC (pick a language BEFORE signing in); the message map is GATED.

    The language switch grants nothing (it sets a display cookie), so it needs no identity — a
    visitor must be able to choose their language on the login page, before any session exists. The
    served message map is a rendering path like any other, so it declares the console's own read
    capability (:func:`messages_capability`) and is refused to anyone the gate would refuse a page:
    deny-by-default is unchanged.
    """
    return {LANGUAGE_PATH: permissions.PUBLIC, MESSAGES_PATH: messages_capability()}


def add_routes(router: APIRouter) -> APIRouter:
    """Add the language-switch route and the served message map to the shell's own router.

    A **GET** link, not a POST: it changes a display preference and grants nothing, so it needs no
    CSRF token (and the login page, before any session exists, has no token to give). The value is
    validated against the real languages; an unknown one changes nothing and just returns the
    visitor where they were.

    The message map is a GET script, GATED by :func:`messages_capability` (the registry reads the
    declaration from :func:`capabilities`, so the gate enforces it like every other route). It is
    served ``no-store`` — a language-shaped body must never sit in a shared cache.
    """

    @router.get(LANGUAGE_PATH, response_class=RedirectResponse)
    async def choose_language(request: Request, lang: str = "", next: str = "/"):
        response = RedirectResponse(_safe_target(next), status_code=303)
        code = normalise_language(lang)
        if code:
            settings = getattr(request.app.state, "webui_session_settings", None) or {}
            response.set_cookie(COOKIE_NAME, code, max_age=COOKIE_MAX_AGE, path="/",
                                secure=bool(settings.get("https_only")), samesite="lax",
                                httponly=True)
        return response

    @router.get(MESSAGES_PATH)
    async def messages(request: Request):
        return Response(messages_script(request), media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})

    return router

