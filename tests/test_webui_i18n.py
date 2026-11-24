"""The admin console's i18n machinery (card I1): extraction stays in step with the templates, the
language of a request follows a stated precedence, a render in one language cannot leak into the
next, and the login page and the shell render in the chosen language.

The extraction half reads the REAL catalogs out of the bot's ``locale/`` tree and the REAL strings
out of the templates and page code, so a template that grows a ``{% trans %}`` without a matching
catalog entry fails here. Babel does the extraction (it is the build tool the card names); if it is
not installed the test falls back to a small scanner that mirrors the two forms this slice uses, so
the suite never skips for want of a developer tool. Babel is declared as a build/test tool in
``requirements.in`` (``babel==2.17.0``); on a machine that runs only the suite, install it with
``pip install babel``.
"""
from __future__ import annotations

import io

import importlib.util
import gettext
import os
import re
import subprocess
import sys
import types
import unicodedata
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell, stub_identity
from services.webservice import i18n
# the write suites' fixtures and harness, reused so this module drives a REAL dialog through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    PASSWORD, build, clean_registry, client, config_dir, recording_bot, sign_in,
)

REPO = Path(__file__).resolve().parent.parent
BABEL_CFG = REPO / "services/webservice/babel.cfg"
#: the templates this migration has wrapped so far: the slice-1 set (the login page and the shell),
#: the slice-2 set (the dashboard and the four list pages, via the shared table component), the
#: slice-3 set (the per-server page, its tabs, and the status mark it shares with its own poll),
#: the slice-4 set (the write dialogs — ``confirm.html``, the ONE dialog every write in the console
#: opens), the slice-5 set (the log card ``_log_card.html`` and ``logs.html`` — card F1), and then
#: card I5's set (the row components ``_strip.html`` / ``_table.html`` and the shell ``base.html``,
#: whose JavaScript strings are served rather than written).
SLICE_TEMPLATES = ("login.html", "base.html", "_language.html", "dashboard.html", "list.html",
                   "_state.html", "_table.html", "_pills.html", "_banner.html",
                   "server_detail.html", "_server_status.html", "confirm.html", "_log_card.html",
                   "logs.html", "_strip.html", "_log_panel.html", "node_log.html",
                   "scheduler_body.html")


# --------------------------------------------------------------------------- extraction harness

def _have_babel() -> bool:
    return importlib.util.find_spec("babel") is not None


def _message_ids(catalog) -> set[str]:
    """Every msgid a catalog carries, with a PLURAL message expanded into its two forms.

    A plural message's ``id`` is a ``(singular, plural)`` TUPLE, not a string — a set comprehension
    over string ids silently drops the pair, which would make the extractors agree by both missing it.
    """
    found: set[str] = set()
    for message in catalog:
        if getattr(message, "obsolete", False):
            continue
        parts = message.id if isinstance(message.id, tuple) else (message.id,)
        found.update(part for part in parts if isinstance(part, str) and part)
    return found


def _extract_with_babel(pot: Path) -> set[str]:
    """The msgids Babel extracts from the console, via the documented command."""
    proc = subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract",
         "-F", str(BABEL_CFG), "-o", str(pot), "--no-wrap", "."],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"pybabel extract failed:\n{proc.stderr}"
    from babel.messages.pofile import read_po
    with pot.open("rb") as handle:
        catalog = read_po(io.BytesIO(handle.read().replace(b"\r\n", b"\n")))
    return _message_ids(catalog)


_TRANS = re.compile(r"\{%-?\s*trans\b[^%]*?-?%\}(.*?)\{%-?\s*endtrans\s*-?%\}", re.S)
_PY_CALL = re.compile(r"""(?:[A-Za-z_][\w.]*\.)?_\(\s*(['"])(.*?)\1""")
#: a Jinja COMMENT (``{# … #}``) is never extracted by Babel; the fallback drops it before matching,
#: so a template that merely MENTIONS ``{% trans %}`` in its doc comment is not mistaken for one.
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
#: Jinja renders ``{% trans count=x %}{{ count }} …`` as the gettext msgid ``%(count)s …``; the
#: fallback mirrors that so it enforces the same invariant as Babel.
_TRANS_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
#: a ``{% pluralize %}`` splits its block into TWO msgids (singular, plural) — Babel's Jinja
#: extractor reads exactly that pair, so the fallback splits on it instead of reading one string.
_TRANS_PLURAL = re.compile(r"\{%-?\s*pluralize\s*-?%\}")


def _python_msgids(path: Path) -> set[str]:
    """``_(…)`` literals in a Python file, via the AST (so implicit concatenation and multi-line
    strings are read whole, the way Babel reads them)."""
    import ast
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "_":
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
            found.add(arg.value)
    return found


def _extract_without_babel() -> set[str]:
    """A fallback scanner for the forms this slice uses — ``{% trans %}`` and ``_("…")``.

    Only reached when Babel is not installed. It reads the SAME files the Babel mapping declares, so
    it enforces the same invariant: the template's ``{% trans %}`` bodies (with ``{{ name }}``
    normalised to gettext's ``%(name)s``, and a ``{% pluralize %}`` body read as its singular and
    plural pair) and the Python ``_()`` literals (read through the AST).
    """
    found: set[str] = set()
    for path in (REPO / "services/webservice").rglob("*"):
        if path.suffix == ".html":
            text = _JINJA_COMMENT.sub("", path.read_text("utf-8"))
            for match in _TRANS.finditer(text):
                found.update(_TRANS_VAR.sub(r"%(\1)s", part).strip()
                             for part in _TRANS_PLURAL.split(match.group(1)))
            found.update(m.group(2) for m in _PY_CALL.finditer(text))
        elif path.suffix == ".py":
            found.update(_python_msgids(path))
    return {msgid for msgid in found if msgid}


def _catalog_msgids(language: str, pot: Path) -> set[str]:
    """The msgids a shipped ``webui`` catalog carries for *language*."""
    po = REPO / "locale" / language / "LC_MESSAGES" / "webui.po"
    assert po.is_file(), f"missing catalog: {po}"
    if _have_babel():
        from babel.messages.pofile import read_po
        with po.open("rb") as handle:
            catalog = read_po(io.BytesIO(handle.read().replace(b"\r\n", b"\n")))
        return _message_ids(catalog)
 # the fallback reads single-line ``msgid``/``msgid_plural`` entries (the catalogs are written
 # UNWRAPPED) and UNESCAPES the PO escaping, so a msgid carrying a literal quote (``"offline"``)
 # matches the extractor's unescaped string. ``_po_unescape`` is defined below; called at call time.
    text = po.read_text("utf-8")
    chunks = re.findall(r'^\s*msgid(?:_plural)?\s+"((?:[^"\\]|\\.)*)"\s*$', text, re.M)
    return {_po_unescape('"' + chunk + '"') for chunk in chunks}


def _extract(pot: Path) -> set[str]:
    return _extract_with_babel(pot) if _have_babel() else _extract_without_babel()


#: the fields of a setup template whose value is translated text (the tile's words).
TEMPLATE_TEXT_FIELDS = ("label", "description")


def _template_msgids() -> set[str]:
    """The setup templates' label and description strings.

    A template's label and description are DATA in its YAML file, translated at render through
    ``translate(template.label)``/``translate(template.description)`` and never wrapped in Python, so
    no extractor sees them. They are read from the same YAML files the setup step loads, which keeps
    the server-side equality meaningful in BOTH directions: a catalog entry with no template behind it
    is stale, and a template that is edited or removed forces the catalogs to follow. A non-string or
    empty field is skipped — such a template does not render a string to translate.
    """
    from ruamel.yaml import YAML

    found: set[str] = set()
    for path in sorted((REPO / "services/webservice/setup/templates").glob("*.yaml")):
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
        for field in TEMPLATE_TEXT_FIELDS:
            value = data.get(field)
            if isinstance(value, str) and value:
                found.add(value)
    return found


def test_every_wrapped_string_is_in_the_shipped_catalogs(tmp_path):
    """Extraction and templates stay IN STEP: what Babel finds is exactly what the catalogs hold."""
    extracted = _extract(tmp_path / "webui.pot") | _template_msgids()
    assert extracted, "the extractor found no translatable strings at all"
 # the wrapped strings are all present (spot-check a few from each slice, so a broken extractor
 # — or a template whose new ``{% trans %}``/``_`` was never extracted — is loud)
    for expected in ("Command Center", "Sign in", "Sign out", "Language", "Dashboard", "Operations",
 # card I7: the breadcrumb's group word — the page label it joins is reused,
 # never a second msgid (the pages' own titles are the ones already listed here)
                     "Cluster",
 # card I8: the environment marker's CHROME words (a node's NAME stays verbatim,
 # it is a `crumb`-style non-chrome part), the log panel's missing-file sentence
 # (a plain `_` sentence rendered where the panel is built), and the
 # log-placement rule (a `say` sentence record carrying the row threshold)
                     "agent", "live state", "no live state", "no node",
                     "bottom when \u2264 {rows} rows, right when more",
                     "The bot log file is not available yet.",
                     "Live state is not available yet. The bot is still starting up.",
 # slice 2: the list pages, the table component, the status words and the
 # sentences that carry figures
                     "Servers", "Nodes", "Instances", "Players", "Node", "Status", "Player",
                     "Search players", "Actions", "RUNNING", "ascending", "descending",
                     "No servers are registered on this cluster.",
                     "%(online)d/%(total)d nodes online",
                     "Filter: name or UCID contains %(query)s · %(filtered)s of %(total)s players",
 # slice 3: the per-server page and its tabs — the tab labels, the config form's
 # labels and help lines, the log card's own words (and the level labels), the
 # missions tab's empty state and the server pill
                     "Overview", "Missions", "Configuration", "Log", "Events",
                     "Configuration — the DCS face", "Identity & access", "Server name",
                     "Discord channels", "Coalition passwords",
                     "Load older", "Start of log", "following while this tab is open",
                     "INFO+", "ALL", "server %(name)s",
                     "This server has no missions configured in its mission list.",
                     "The mission list could not be read.",
                     "A channel change is applied immediately: the bot rewrites servers.yaml and "
                     "starts using the new channel at once, with no restart.",
 # slice 4: the write dialogs — confirm.html's own chrome, the action labels and
 # the dialog copy the page modules compose (the title pattern, the buttons, the
 # option labels, the warning and the "what will happen" lines)
                     "Confirming: %(go)s.", "Cancel",
                     "Cancel — go back without doing anything (Esc)",
                     "Selected missions", "{label} {target}?", "{label} (node)",
                     "Target: <span class=\"target\">%(target)s</span> — the action and your name are "
                     "written to the bot log.",
                     "Target: <span class=\"target\">%(target)s</span> — the action and your name are "
                     "written to the bot log, and it cannot be undone from here.",
                     "Startup", "Shutdown", "Restart server", "Start up server",
                     "Shut down {server} — {players} player(s) lose their connection",
                     "Restart {server} — players flying lose their sortie",
                     "End maintenance so the server is scheduled again",
                     "Take the servers on {node} offline?",
                     "Bring the servers on {node} back online?",
                     "Kick", "Ban", "Reason", "Days (optional)",
                     "Remove {count} missions from {server}?",
                     "<b>The running mission ({mission}) cannot be removed.</b>",
                     "Last update check: {when}.",
 # slice 5 (card F1): the Logs page — the log card's own chrome (shared with the
 # dashboard) and the Logs page's lead constant
                     "Recent log", "Follow: newest", "Shown: %(level)s and up",
                     'log position <b>%(position)s</b> — longest list %(rows)s row(s), rule '
                     '"%(rule)s"',
                     "The bot log, full width, with the same level filter and placement as the "
                     "dashboard.",
                     "Every node in the cluster, with the instances and servers it carries. Acting "
                     "on a node takes every server on it down with it, so check the servers before "
                     "you act.",
 # slice 5 (card I5): the row buttons (the strip and the download control), the
 # served JavaScript map, the strip's own chrome, and the two trimmed leads
                     "Restart — players currently flying lose their sortie", "Restart server {server}",
                     "start a STOPPED server — Startup is the usual one",
                     "Maintenance — keep this server out of service; the scheduler will not start it",
                     "Ban {player} from {server}",
                     "Controls for node {node}", "Controls for {player} on {server}",
                     "More controls for {server}", "More controls",
                     "Download the bot log of node {node}",
                     "Download log — hand over {node}'s own bot log file as a download",
                     "{label} — an action on {name} is running; this can take a moment",
                     "Start and Stop live only here: they act on fewer states than Startup and "
                     "Shutdown, so they do not earn a place in the strip.",
                     "no update check yet",
                     "Live updates: {status}", "Live updates: unavailable",
                     "Every server on every node, in full. A row you may operate carries its "
                     "controls on the row; acting on one affects that server alone.",
 # card I9: the last unwrapped visible chrome — the notice's audit warning
 # ({% trans %}), the mission picker's field copy (the label's SOURCE was
 # unmarked, so the render through `_(field.label)` saw no msgid), and the three
 # log-window JSON messages the log follower swaps into the DOM verbatim
                     "The audit entry for this action could not be recorded — see the bot log.",
                     "Mission file already on this server",
                     "Only files NOT already in the rotation are offered.",
                     "An offset or a behind position is required.",
                     "The offset must be a non-negative whole number.",
                     "The behind position must be a non-negative whole number.",
 # card I9: the log-window endpoint's OTHER JSON messages — the same class, each
 # an i18n._-marked TEMPLATE whose operator data (node, path, error) is
 # interpolated AFTER the sentence is translated
                     "The Events tab is only offered while the debug plugin is active for this server.",
                     "No log file yet for this server (looked for '{path}' on node '{node}').",
                     "The log restarted while loading older lines — the lines shown are from the "
                     "previous file.",
                     "Node '{node}' is offline or unknown, so its log cannot be read from this console.",
                     "Node '{node}' cannot read this server's log file (permission denied).",
                     "Node '{node}' did not answer in time, so the log was not read.",
                     "Node '{node}' could not hand over the log ({error}).",
                     "Node '{node}' answered with a failure code instead of the log.",
                     "No log directory yet for this server (looked for '{directory}' on node "
                     "'{node}').",
                     "Node '{node}' cannot list this server's log directory (permission denied).",
                     "Node '{node}' did not answer in time, so the log files were not listed.",
                     "Node '{node}' could not list the log files ({error}).",
 # card I10: the Config tab's own chrome (the mission-list field's HELP — the
 # render is already wired via `_(f.help)`, the SOURCE was unmarked — and the two
 # apply notes the tab prints, whose render is wired here too) …
                     "The missions DCS cycles through, in order. Managed by the mission autoscan and "
                     "validated at boot — a bad path is a boot failure — so this tab does not edit it.",
                     "A coalition password can be changed while the server is running, paused or "
                     "stopped: the bot sends it to DCS and keeps the cleartext in its database.",
                     "The coalition passwords can only be changed while the server is shutdown, "
                     "stopped, running or paused. Wait for it to settle, then edit here.",
                     "The server must be stopped to change its configuration. Stop it on the Servers "
                     "page, then edit here.",
 # card I10: the console's OWN 403 refusals, surfaced into the page by the
 # one-shot notice (``_notice.html``, the target ``shell.js`` swaps in). The
 # action's own message is NOT here — it is composed where the action runs and
 # shown on Discord too, so it renders verbatim (never looked up in a catalog).
                     "Not authorized (this server is outside your scope).",
                     "Not authorized (this target is outside your scope).",
                     "Not authorized (no signed-in identity).",
                     "Not authorized (this action must be confirmed through its dialog).",
 # card I10 (an extra spot the earlier sweeps missed — the same class): the
 # console's OWN \"no Revert could be offered\" sentence, rendered verbatim by
 # ``server_detail.html`` before this card
                     "The previous values are too long to offer a one-shot Revert here. Re-open the "
                     "page to edit them back by hand.",
 # card I11: the i18n tail — the TAB TOOLTIP (a sentence RECORD rendered through
 # the guarded `say`, whose figure is interpolated at render), and the shell's two
 # standalone error documents (refusal_page / retryable_page), whose copy is
 # composed in Python and translated at render through the request's environment.
 # A Python `_` on each is only the extraction marker.
                     "{count} node(s) offline", "{count} server(s) not running",
                     "access denied", "Access denied", "Return to the dashboard",
                     "Your account does not have access to this page.",
                     "Your request could not be completed, and nothing was saved.",
                     "verifying your identity", "The console is starting up",
                     "Retrying automatically&hellip;", "Retry now",
                     "Still not ready. Retry by hand when you are ready.",
                     "The answer changed. Reload to continue.",
                     "You are still signed in &mdash; no new sign-in is needed. The console has just "
                     "started and is verifying your identity; this page will retry by itself."):
        assert expected in extracted, f"'{expected}' was not extracted"
    for language in ("de", "es"):
        catalog = _catalog_msgids(language, tmp_path / "webui.pot")
        assert catalog == extracted, (
            f"the {language} catalog is out of step with the templates/Python; "
            f"missing={sorted(extracted - catalog)}, stale={sorted(catalog - extracted)}")


def test_babel_and_the_independent_scanner_extract_the_same_strings(tmp_path):
    """Two INDEPENDENT extractors must agree — so a wrapped string one of them misses is loud.

    ``test_every_wrapped_string_is_in_the_shipped_catalogs`` catches a wrapped string that is missing
    from the CATALOG; this catches one that is not EXTRACTED at all: Babel (a real Jinja/Python parse)
    and the fallback scanner (regex + AST) are different mechanisms, and a ``{% trans %}``/``_()`` that
    only one of them sees is a divergence, which fails here rather than passing vacuously (both would
    simply lack the string). It is the "a wrapped string not extracted must fail" pin for this slice.
    """
    without = _extract_without_babel()
    assert without, "the independent scanner found no translatable strings at all"
    if _have_babel():
        with_babel = _extract_with_babel(tmp_path / "webui.pot")
        assert with_babel == without, (
            "the extractors disagree — a wrapped string was not extracted by one of them; "
            f"babel-only={sorted(with_babel - without)}, scanner-only={sorted(without - with_babel)}")
    else:  # pragma: no cover - Babel is a developer tool; the scanner still enforces the invariant
        for expected in ("Command Center", "Search players", "ascending", "Overview", "Load older"):
            assert expected in without, f"'{expected}' was not extracted"


def test_the_slice_templates_are_wrapped():
    """Every template this migration claims to have wrapped actually carries a wrapped string.

    A no-op guard on :data:`SLICE_TEMPLATES` itself: the extraction checks compare the CATALOG against
    what the extractors find, so a template added to the list but left unwrapped would still pass
    them silently. This fails instead — a new slice cannot be *claimed* without being done.
    """
    templates = REPO / "services/webservice/templates"
    for name in SLICE_TEMPLATES:
        text = (templates / name).read_text("utf-8")
        assert "{% trans" in text or "_(" in text, f"{name} carries no wrapped string"


#: the languages that ship a ``webui`` catalog. English is the source language and every English
#: string is its own msgid, so it ships none — it is asserted through the lookup, never a file.
_CATALOG_LANGUAGES = ("de", "es", "ru", "cn")


def test_no_shipped_catalog_carries_a_fuzzy_entry():
    """No ``#, fuzzy`` entry in ANY catalog.

    gettext DROPS a fuzzy entry when the ``.po`` is compiled, so the msgid renders at runtime and the
    operator sees ENGLISH — a translation that looks present in the source but never reaches the page.
    This class of defect reached review twice (most recently the Settings restart banner in all four
    non-English catalogs); the tests that asserted the English literal are exactly why it passed. A
    ``fuzzy`` flag on anything shipped is therefore a failure, not a note.
    """
    for language in _CATALOG_LANGUAGES:
        po = REPO / "locale" / language / "LC_MESSAGES" / "webui.po"
        assert po.is_file(), f"missing catalog: {po}"
        offenders = []
        for number, line in enumerate(po.read_text("utf-8").splitlines(), start=1):
            stripped = line.strip()
 # only a FLAG line (``#, fuzzy`` / ``#, fuzzy, python-format``) counts — a msgstr that
 # merely contains the word does not
            if stripped.startswith("#,") and "fuzzy" in stripped:
                offenders.append(f"{po.name}:{number}: {stripped}")
        assert not offenders, "fuzzy entries are dropped by gettext and render English: " + \
            "; ".join(offenders)


def _read_po_pairs(path: Path) -> list[tuple[str, str]]:
    """Every ``(msgid, msgstr)`` in the catalog at *path*, the header entry included.

    ONE reader for a shipped catalog and for a synthetic one, so the positive control below
    exercises the SAME code path the real check uses rather than a re-implementation of it.
    """
    assert path.is_file(), f"missing catalog: {path}"
    if _have_babel():
        from babel.messages.pofile import read_po
        with path.open("rb") as handle:
            catalog = read_po(io.BytesIO(handle.read().replace(b"\r\n", b"\n")))
        return [(message.id, message.string if isinstance(message.string, str) else "")
                for message in catalog
                if isinstance(message.id, str) and not getattr(message, "obsolete", False)]
    pairs: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", path.read_text("utf-8")):  # PO entries are blank-line separated
        msgid = _po_field(block, "msgid")
        if msgid is None:
            continue
        pairs.append((msgid, _po_field(block, "msgstr") or ""))
    return pairs


#: a handful of shipped msgids English must resolve to THEMSELVES — the lookup, not a file.
_ENGLISH_SAMPLES = ("Command Center", "Sign in", "Sign out", "Language", "Dashboard", "Cancel")


def test_english_serves_its_own_msgid_and_ships_no_catalog(tmp_path):
    """English is the SOURCE LANGUAGE: every lookup hands back the msgid, and no ``en`` catalog exists.

    English IS the msgid, so there is nothing to translate and nothing to ship. The defect this pins
    is a source-language catalog that carries a translation: the lookup then serves a string that is
    not the English source, and the console renders another label's words (``Add server`` came out as
    ``DCS servers``; ``Status channel id`` as ``audit channel``). Such an entry is not ``#, fuzzy``, so
    gettext does NOT drop it (``test_no_shipped_catalog_carries_a_fuzzy_entry`` cannot see it), and a
    test that asserts the English literal cannot either — the catalog overrides the literal at render.
    So the check runs over the LOOKUP: English must resolve each shipped msgid to itself, and the
    ``en`` catalog must be ABSENT, so a regenerated identity catalog is caught here rather than
    shipping silently.

    The POSITIVE CONTROL at the end runs the SAME lookup over a synthetic catalog that overrides one
    msgid with another label's words, and asserts the override is served. A reader that found nothing
    (a wrong domain, a wrong localedir) would hand back the msgid there too, so every assertion above
    would pass vacuously — it fails HERE instead.
    """
    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo

    translations = gettext.translation("webui", localedir=str(REPO / "locale"), languages=["en"],
                                       fallback=True)
    shipped = {msgid for msgid, _msgstr in _catalog_pairs("de")}
    for msgid in _ENGLISH_SAMPLES:
        assert msgid in shipped, f"{msgid!r} is not a shipped msgid — the sample proves nothing"
        served = translations.gettext(msgid)
        assert served == msgid, (
            f"English serves {served!r} for {msgid!r}: English is the source language, so every "
            "lookup must hand back its own msgid")

    catalog = REPO / "locale" / "en" / "LC_MESSAGES" / "webui.po"
    assert not catalog.exists(), (
        f"{catalog} exists: English is the msgid and ships no catalog, so a regenerated identity "
        "catalog would serve a string that is not the English source")

 # the positive control: the SAME reader, over a synthetic catalog carrying one translation.
    synthetic = Catalog(locale="xx")
    synthetic.add("Add server", "DCS servers")
    synthetic.add("Delete server", "")
    directory = tmp_path / "locale" / "xx" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "webui.mo").open("wb") as handle:
        write_mo(handle, synthetic)
    control = gettext.translation("webui", localedir=str(tmp_path / "locale"), languages=["xx"])
    assert control.gettext("Add server") == "DCS servers", \
        "the lookup did not serve a translation the catalog carries (it reads nothing: vacuous)"
    assert control.gettext("Delete server") == "Delete server", \
        "an empty msgstr must fall back to the msgid"


def test_the_restart_banner_and_the_layout_label_are_really_translated():
    """The banner and the layout-chooser label survive COMPILATION and render in every language.

    Checked through gettext on the real ``.mo`` files (not the ``.po``): the point of the fuzzy test
    above is that a ``.po`` can carry a translation gettext never serves. The English base stays the
    msgid; the other four must differ from it.
    """
    from services.webservice.pages import settings as settings_page

    for language in ("de", "es", "ru", "cn"):
        translations = gettext.translation("webui", localedir=str(REPO / "locale"),
                                           languages=[language], fallback=True)
        assert translations.gettext(settings_page.SAVED_RESTART) != settings_page.SAVED_RESTART, \
            f"the restart banner is not translated in {language}"
        assert translations.gettext("List or single") != "List or single", \
            f"the layout-chooser label is not translated in {language}"


def test_the_draft_german_catalog_is_marked_as_a_draft():
    """The card requires the German catalog to SAY it is a machine-assisted, unreviewed draft."""
    header = (REPO / "locale/de/LC_MESSAGES/webui.po").read_text("utf-8")
    assert "DRAFT" in header and "NOT REVIEWED BY A NATIVE SPEAKER" in header


def test_the_draft_spanish_catalog_is_marked_as_a_draft_and_informal():
    """Card L-ES: the Spanish catalog SAYS it is an unreviewed draft and names its informal register."""
    header = (REPO / "locale/es/LC_MESSAGES/webui.po").read_text("utf-8")
    assert "DRAFT" in header and "NOT REVIEWED BY A NATIVE SPEAKER" in header
    assert "INFORMAL" in header and "tú" in header


def test_the_draft_chinese_catalog_is_marked_as_a_draft_and_informal():
    """Card L-CN: the Chinese catalog SAYS it is an unreviewed draft, names its informal register
    (你, never 您), and matches the bot's own language code for Chinese (`cn`, not `zh`)."""
    header = (REPO / "locale/cn/LC_MESSAGES/webui.po").read_text("utf-8")
    assert "DRAFT" in header and "NOT REVIEWED BY A NATIVE SPEAKER" in header
    assert "INFORMAL" in header and "你" in header and "您" in header
    assert "`cn`" in header or "cn/LC_MESSAGES" in header


# --------------------------------------------------------------------------- placeholder parity

#: A placeholder in these catalogs is either gettext's ``%(name)s`` (the form Babel writes for a
#: Jinja ``{% trans %}`` body) or the Python brace form ``{name}`` (the dialog sentence records'
#: ``.format`` placeholders, carried by the ``_`` literals ``pages/actions`` composes). A bare
#: ``%``/``{`` that matches neither is not a placeholder and is ignored.
_PLACEHOLDER = re.compile(r"%\((\w+)\)|\{(\w+)\}")


def _placeholders(text: str) -> dict[str, int]:
    """The placeholder names in *text* with their counts (a repeated name is meaningful)."""
    counts: dict[str, int] = {}
    for match in _PLACEHOLDER.finditer(text or ""):
        name = match.group(1) or match.group(2)
        counts[name] = counts.get(name, 0) + 1
    return counts


_PO_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _po_unescape(chunks: str) -> str:
    """The string value of a ``"…" "…"`` run of PO chunks (continuations joined)."""
    text = "".join(_PO_QUOTED.findall(chunks))
    return (text.replace("\\\\", "\x00").replace('\\"', '"').replace("\\n", "\n")
            .replace("\\t", "\t").replace("\x00", "\\"))


def _po_field(block: str, keyword: str) -> str | None:
    """The logical value of *keyword* in one PO entry block, continuations included."""
    match = re.search(rf'^{keyword}\s+((?:"(?:[^"\\]|\\.)*"\s*)+)', block, re.M)
    return _po_unescape(match.group(1)) if match else None


def _catalog_pairs(language: str) -> list[tuple[str, str]]:
    """Every ``(msgid, msgstr)`` in *language*'s ``webui`` catalog, the header entry included."""
    return _read_po_pairs(REPO / "locale" / language / "LC_MESSAGES" / "webui.po")


def test_every_translation_keeps_its_msgid_s_placeholders():
    """PLACEHOLDER PARITY: every ``msgstr`` carries exactly its ``msgid``'s placeholders.

    A translation that OMITS a placeholder (``"Mission laden"`` for ``"Loading {mission}"``) renders
    with the server or mission name silently gone — undetectable at render time, so it is caught here
    against the real catalogs instead. The check is a strict placeholder NAME -> COUNT match, in every
    ``webui`` catalog, the header entry included; it is 372/372 today. Nothing was enforcing it.

    WHAT IT DOES NOT COVER, stated so a green run is not misread: it proves only that the placeholders
    are PRESENT and unchanged. It says nothing about whether a translation is correct, well written or
    idiomatic — a catalog can pass here and still be wrong in every other way.
    """
    total = 0
    for language in ("de", "es"):
        pairs = _catalog_pairs(language)
        assert pairs, f"the {language} catalog is empty"
        for msgid, msgstr in pairs:
            total += 1
            if not msgstr:
                continue  # an untranslated entry falls back to the msgid: nothing to check
            assert _placeholders(msgstr) == _placeholders(msgid), (
                f"{language}: the translation changes the placeholders of {msgid!r} — "
                f"msgid {sorted(_placeholders(msgid).items())} vs "
                f"msgstr {sorted(_placeholders(msgstr).items())}")
    assert total >= 372, f"only {total} catalog entries were checked"


# --------------------------------------------------------------------------- the valid language set

def test_the_language_control_offers_only_languages_with_a_catalog(shell_app):
    app, registrar = shell_app
    languages = i18n.selectable_languages()
    assert languages[0] == "en", "English (the msgid) must be the first, default option"
    assert "de" in languages
    assert "es" in languages
    assert "ru" in languages
    assert "cn" in languages
    assert "fr" in languages
    assert "it" in languages
    assert "pt" in languages
    assert "pl" in languages
 # a code with no ``webui`` catalog must never be offered (card L-CN: cn now HAS one, so the
 # invariant is proved with codes that name no catalog at all)
    for code in ("zh", "ja"):
        assert code not in languages
    control = i18n.language_control(_request(app, language="de"))
    assert [option["code"] for option in control["languages"]] == list(languages)
    assert [option["active"] for option in control["languages"]] == \
        [code == "de" for code in languages]


def test_the_validator_refuses_anything_that_is_not_a_real_language():
    assert i18n.normalise_language("de") == "de"
    assert i18n.normalise_language("de-DE") == "de"
    assert i18n.normalise_language("en_US") == "en"
    assert i18n.normalise_language("xx") is None
    assert i18n.normalise_language("../../etc/passwd") is None
    assert i18n.normalise_language("de\nX-Injected: 1") is None
    assert i18n.normalise_language("de" * 64) is None
    assert i18n.normalise_language("") is None
    assert i18n.normalise_language(None) is None


# --------------------------------------------------------------------------- the precedence

def test_the_precedence_is_stored_then_header_then_configured_default(shell_app):
    app, _ = shell_app
    app.state.webui_default_language = "de"
 # no cookie, no header: the install-wide default from main.yaml
    assert i18n.resolve_language(_request(app)) == "de"
 # an Accept-Language header outranks the configured default
    assert i18n.resolve_language(_request(app, header="en")) == "en"
 # the stored choice outranks the header
    assert i18n.resolve_language(_request(app, language="de", header="en")) == "de"
 # a region-qualified header is read as its base language
    assert i18n.resolve_language(_request(app, header="de-DE,de;q=0.9,en;q=0.8")) == "de"


def test_an_unknown_or_absent_preference_falls_back(shell_app):
    app, _ = shell_app
    app.state.webui_default_language = "de"
 # an unknown cookie is skipped (never trusted), the header supplies the answer
    assert i18n.resolve_language(_request(app, language="zz", header="en")) == "en"
 # an unknown cookie AND an unsupported header fall through to the configured default
    assert i18n.resolve_language(_request(app, language="klingon", header="ja-JP")) == "de"
 # a path-traversal cookie is refused like any other unknown value
    assert i18n.resolve_language(_request(app, language="../../etc", header="ja")) == "de"
 # nothing at all, and English configured: English
    app.state.webui_default_language = "en"
    assert i18n.resolve_language(_request(app)) == "en"


def test_the_default_language_comes_from_main_yaml(tmp_path):
    (tmp_path / "main.yaml").write_text("language: de\n", encoding="utf-8")
    assert i18n.default_language(tmp_path) == "de"
    (tmp_path / "main.yaml").write_text("language: de_DE\n", encoding="utf-8")
    assert i18n.default_language(tmp_path) == "de"
 # a value naming no ``webui`` catalog is not trusted: English
    (tmp_path / "main.yaml").write_text("language: xx\n", encoding="utf-8")
    assert i18n.default_language(tmp_path) == "en"
 # a missing file (or a missing key) is not an error
    assert i18n.default_language(tmp_path / "nowhere") == "en"
    (tmp_path / "main.yaml").write_text("verbose: true\n", encoding="utf-8")
    assert i18n.default_language(tmp_path) == "en"


# --------------------------------------------------------------------------- the pages render

@pytest.mark.asyncio
async def test_the_login_page_renders_in_the_chosen_language(shell_app):
    app, _ = shell_app
    async with asgi_client(app) as client:
        english = await client.get("/auth/login")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        german = await client.get("/auth/login")
    assert "No sign-in method is configured" in english.text
    assert "Keine Anmeldemethode konfiguriert" in german.text
    assert 'lang="de"' in german.text and 'lang="en"' in english.text
 # the language control is on the login page (a visitor picks BEFORE signing in): the pill names
 # the language in force and the menu marks it, every row drawn as flag plus its own endonym.
    assert 'class="langchip"' in german.text
    assert 'class="mi on"' in german.text and ">Deutsch<" in german.text


def test_the_shell_renders_in_the_chosen_language(shell_app):
    app, registrar = shell_app
    nav = [{"label": "Operations",
            "links": [{"label": "Dashboard", "url": "/", "active": True},
                      {"label": "Logs", "url": "/logs", "active": False}]}]
    german = i18n.render(_request(app, language="de"), "base.html", nav_groups=nav)
    english = i18n.render(_request(app, language="en"), "base.html", nav_groups=nav)
    assert "Übersicht" in german and "Betrieb" in german and "Protokolle" in german
    assert "Dashboard" not in german and "Operations" not in german
    assert "Dashboard" in english and "Operations" in english
 # both documents carry the control, offering exactly the real languages
    assert 'class="mi on"' in german and "Deutsch" in german


def test_the_breadcrumb_is_translated_but_a_target_name_is_not(shell_app):
    """Card I7: the top bar's breadcrumb is CHROME — its words are translated, its target is not.

    The breadcrumb was one composed string, which is why it stayed English. It is now PARTS: each
    chrome word is the page's own wrapped label rendered through the request's ``_`` (so the page
    label is reused, never duplicated as a second msgid), and a target NAME (a server, a node, a
    mission) is printed VERBATIM — operator data is never looked up. The last assertion proves the
    verbatim half: a target that happens to spell a catalog msgid is still printed as typed.
    """
    from services.webservice.pages import dashboard as dashboard_page
    app, _ = shell_app
 # the dashboard's own breadcrumb: "Cluster / Dashboard", its label the page's real title
    pages = dashboard_page.crumb(dashboard_page.CRUMB_GROUP, dashboard_page.DASHBOARD_TITLE)
    german = i18n.render(_request(app, language="de"), "base.html", crumb=pages)
    spanish = i18n.render(_request(app, language="es"), "base.html", crumb=pages)
    english = i18n.render(_request(app, language="en"), "base.html", crumb=pages)
    assert "Cluster / Übersicht" in german
    assert "Clúster / Panel" in spanish
    assert "Cluster / Dashboard" in english
 # the per-server crumb: two chrome words translated, the target NAME untouched. "Restart" is a
 # real msgid (de "Neustart"); as a server's name it must NOT be translated.
    with_target = dashboard_page.crumb(dashboard_page.CRUMB_GROUP, "Servers", target="Restart")
    rendered = i18n.render(_request(app, language="de"), "base.html", crumb=with_target)
    assert "Cluster / Server / Restart" in rendered
    assert "Neustart" not in rendered


# --------------------------------------------------------------------------- the leak test

def test_a_render_in_one_language_cannot_change_the_next(shell_app):
    """Two renders in two languages, back to back: neither influences the other.

    This is what the per-language JINJA ENVIRONMENT buys — no shared, mutable translations object
    for a render to leave behind. The environments are distinct objects and the base language reuses
    the shell's own (English) environment.
    """
    app, registrar = shell_app
    nav = [{"label": "Operations", "links": [{"label": "Dashboard", "url": "/", "active": True}]}]
    first_german = i18n.render(_request(app, language="de"), "base.html", nav_groups=nav)
    english = i18n.render(_request(app, language="en"), "base.html", nav_groups=nav)
    second_german = i18n.render(_request(app, language="de"), "base.html", nav_groups=nav)
    assert "Übersicht" in first_german and "Übersicht" not in english
    assert "Dashboard" in english and "Dashboard" not in first_german
    assert first_german == second_german, "the English render changed what the next German one saw"
    assert english == i18n.render(_request(app, language="en"), "base.html", nav_groups=nav)
 # the mechanisms are genuinely distinct objects (not one environment mutated in place)
    assert i18n.environment_for_language(registrar, "de") is not \
        i18n.environment_for_language(registrar, "en")
    assert i18n.environment_for_language(registrar, "en") is registrar.environment


# --------------------------------------------------------------------------- the switch route

@pytest.mark.asyncio
async def test_the_switch_route_stores_the_choice_and_returns_to_a_safe_path(shell_app):
    app, _ = shell_app
    async with asgi_client(app) as client:
        chosen = await client.get("/language", params={"lang": "de", "next": "/logs"},
                                  follow_redirects=False)
        assert chosen.status_code == 303
        assert chosen.headers["location"] == "/logs"
        cookie = chosen.headers["set-cookie"]
        assert "dcssb_lang=de" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
 # an unknown language changes nothing, and an off-site target is refused
        unknown = await client.get("/language", params={"lang": "zz", "next": "//evil.example.com"},
                                   follow_redirects=False)
        assert unknown.status_code == 303
        assert unknown.headers["location"] == "/"
        assert "set-cookie" not in unknown.headers


# --------------------------------------------------------------------------- the choice survives

@pytest.mark.asyncio
async def test_the_chosen_language_survives_a_sign_in(tmp_path):
    """The stored choice is its OWN cookie, so the session-fixation clear at sign-in cannot lose it."""
    from services.webservice import session as session_module
    from services.webservice.auth import local as local_auth

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text("roles:\n  Admin:\n    - specialk\n",
                                                    encoding="utf-8")
    password = "correct horse battery staple"
    config = {
        "session": {"secret": "s" * 40, "cookie_name": "dcssb_test_session", "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True,
                           "users": [{"username": "specialk",
                                      "password_hash": local_auth.hash_password(
                                          password, iterations=local_auth.MIN_ITERATIONS),
                                      "roles": ["Admin"]}]},
                 "discord": {"enabled": False}},
    }
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(tmp_path)),
                  config=config)
    async with asgi_client(app) as client:
 # the visitor picks German BEFORE signing in
        await client.get("/language", params={"lang": "de", "next": "/auth/login"})
        page = await client.get("/auth/login")
        assert 'lang="de"' in page.text
        token = re.search(rf'name="{session_module.CSRF_FIELD}" value="([^"]+)"',
                          page.text).group(1)
        login = await client.post("/auth/login",
                                  data={session_module.CSRF_FIELD: token, "username": "specialk",
                                        "password": password, "next": "/"},
                                  follow_redirects=False)
        assert login.status_code == 303
 # the sign-in cleared the SESSION, not the language choice
        after = await client.get("/auth/login")
        assert 'lang="de"' in after.text, "the language choice was lost at sign-in"


# --------------------------------------------------------------------------- the flag control

def test_the_language_control_names_the_current_language_and_offers_the_rest_in_a_menu(shell_app):
    """One pill naming the language in force, and a popover menu with one row per offered language.

    Each row draws the flag AND its own endonym, so a flag no longer has to carry the name alone;
    the row in force is marked, and every row is a plain GET link. English (the source language) gets
    the NEUTRAL globe rather than a national flag, and the menu is a native popover opened by the
    pill's own button — no script, so the control survives with JavaScript off.
    """
    app, _ = shell_app
    german = i18n.render(_request(app, language="de"), "base.html")
    english = i18n.render(_request(app, language="en"), "base.html")

    # exactly the compiled-catalog languages, one row each
    for code in i18n.selectable_languages():
        assert f'hreflang="{code}"' in german, code
    assert german.count('role="menuitem"') == len(i18n.selectable_languages())

    # the pill names the language in force: German's own flag and its own endonym
    assert 'class="langchip"' in german
    assert 'popovertarget="lang-menu"' in german and ' popover ' in german
    assert 'aria-haspopup="menu"' in german
    assert 'src="/static/flags/de.svg"' in german and ">Deutsch<" in german

    # the active row is the one in force, marked by more than its colour
    assert german.count('class="mi on"') == 1
    assert 'aria-current="true"' in german

    # every row is a plain GET link to the switch route — never a form, never a script
    for code in i18n.selectable_languages():
        assert f"/language?lang={code}&amp;next=" in german, code
    assert 'aria-label="Deutsch"' not in german, "the name is drawn now, not only announced"

    # English: the NEUTRAL globe, never a national flag
    assert 'src="/static/flags/globe.svg"' in german
    # the flag images are decorative (alt=""), because the row already draws the name
    assert 'alt=""' in german
    # both documents carry the control (English too)
    assert 'class="langchip"' in english and 'src="/static/flags/globe.svg"' in english


def test_the_flag_assets_are_vendored_and_declared():
    """The assets exist as real files, and each offered language with a flag names an existing one."""
    flags_dir = REPO / "services/webservice/static/flags"
    assert (flags_dir / i18n.FLAG_NEUTRAL_ASSET).is_file(), "the neutral globe asset is missing"
    for code, asset in i18n.LANGUAGE_FLAGS.items():
        assert (flags_dir / asset).is_file(), f"{code} names a missing asset {asset}"
    assert (flags_dir / "SOURCE.txt").is_file(), "the assets' source/licence note is missing"


# --------------------------------------------------------------------------- the tidy-ups

def test_an_uppercase_language_code_is_accepted_and_normalised():
    """``DE`` is accepted and read as ``de`` — the validator case-folds before it compares."""
    assert i18n.normalise_language("DE") == "de"
    assert i18n.normalise_language("DE-DE") == "de"
    assert i18n.normalise_language("En") == "en"


def test_the_catalog_list_is_cached_and_picks_up_a_new_language(monkeypatch, tmp_path):
    """``catalog_languages`` is cached, but a NEW compiled catalog appears without a restart.

    The cache is keyed by ``locale/``'s own mtime: compiling a new language creates a directory under
    it, which changes that mtime, so the next call rescans. This proves both halves — the cached
    answer is reused while the tree is unchanged, and a change is seen at once.
    """
    def _catalog(language: str) -> None:
        directory = tmp_path / language / "LC_MESSAGES"
        directory.mkdir(parents=True)
        (directory / f"{i18n.DOMAIN}.mo").write_bytes(b"")

    _catalog("de")
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    monkeypatch.setattr(i18n, "_CATALOG_CACHE", {})

    first = i18n.catalog_languages()
    assert first == ("de",)
    assert i18n.catalog_languages() is first, "the cached tuple must be reused, not rebuilt"

 # a NEW language: the directory appears, so ``locale/``'s mtime changes — picked up at once.
 # The mtime is set EXPLICITLY (a fixed value far from "now") so the test does not depend on the
 # filesystem's timestamp granularity: the mechanism is "re-scan when the signature changes".
    _catalog("fr")
    os.utime(tmp_path, (1_000_000_000, 1_000_000_000))
    assert i18n.catalog_languages() == ("de", "fr")
    assert "fr" in i18n.selectable_languages()


# --------------------------------------------------------------------------- the pages render in German

@pytest.mark.asyncio
async def test_the_dashboard_and_the_list_pages_render_in_german(tmp_path):
    """The rendered check: with German chosen, the dashboard and the list pages are German.

    English stays the msgid, so the English render is byte-for-byte what it was — that is what the
    other suites assert; here the German is proved end to end (heading, table header, status word,
    pill, search form labels and an empty state).
    """
    from test_webui_dashboard import (BOT_YAML, Status, _build, _hash, _login, _source, node,
                                      player, server)

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    provider = lambda: _source(servers=[server(name="Alpha", status=Status.RUNNING,
                                               players=[player("Viper-1")])],
                               node_obj=node(instances={}))  # noqa: E731 - no instances: empty state
    app, _registrar = _build(tmp_path, users=[
        {"username": "staff", "password_hash": _hash(), "roles": ["DCS Admin"]}])
    app.state.webui_source_provider = provider

    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        dashboard = (await client.get("/")).text
        servers = (await client.get("/fleet")).text
        instances = (await client.get("/instances")).text
        players = (await client.get("/players")).text

 # the dashboard: the heading, a column header, the status word and a pill are German
    assert "Übersicht" in dashboard and ">Dashboard<" not in dashboard
    assert "Knoten <span" in dashboard, "the Node column header is not German"
    assert "LÄUFT" in dashboard, "the RUNNING status word is not German"
    assert "Knoten online" in dashboard, "the nodes pill is not German"

 # a list page: its heading and a table header are German
    assert "<h1>Server</h1>" in servers and "Verstrichen" in servers

 # the empty state is the read models' own sentence, in German
    assert "Es sind keine Instanzen konfiguriert." in instances

 # the search form's labels and buttons are German
    assert "Spieler suchen" in players and ">Suchen<" in players and ">Zurücksetzen<" in players


# --------------------------------------------------------------------------- the server page renders

@pytest.mark.asyncio
async def test_the_server_page_and_its_tabs_render_in_german(tmp_path):
    """The rendered check: with German chosen, the per-server page and its tabs are German.

    English stays the msgid, so the English render is byte-for-byte what it was — that is what the
    other suites assert; here the German is proved end to end: the tab strip, the shared status word,
    the Overview card's own copy, the Configuration tab's group and field labels and the Missions
    tab's empty state. The OPERATOR DATA (the server name and the mission name) stays exactly as it is.
    """
    from test_webui_dashboard import (BOT_YAML, Status, _build, _hash, _login, _source, node, player,
                                      server)

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    provider = lambda: _source(  # noqa: E731 - the source seam, exactly as the other pages use it
        servers=[server(name="Alpha", status=Status.RUNNING, players=[player("Viper-1")])],
        node_obj=node(instances={}))
    app, _registrar = _build(tmp_path, users=[
        {"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}])
    app.state.webui_source_provider = provider

    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        overview = (await client.get("/fleet/Alpha")).text
        config = (await client.get("/fleet/Alpha?tab=configuration")).text
        missions = (await client.get("/fleet/Alpha?tab=missions")).text

 # the page chrome: the tab strip is German (and so is the server pill, built from the name)
    assert ">Übersicht</a>" in overview and ">Spieler</a>" in overview
    assert ">Missionen</a>" in overview and ">Protokoll</a>" in overview
    assert ">Konfiguration</a>" in overview
    assert ">Overview</a>" not in overview and ">Configuration</a>" not in overview
    assert "Server Alpha" in overview, "the server pill is not German"

 # the shared status word and the Overview tab's own copy are German
    assert "LÄUFT" in overview, "the RUNNING status word is not German"
    assert "Aktuelle Mission:" in overview and "Laufzeit" in overview
    assert "Knoten" in overview, "the node/instance status row is not German"

 # operator data is untouched
    assert "Caucasus_Training" in overview, "the mission name must not be translated"
    assert "/fleet/Alpha" in overview

 # the Configuration tab: the heading, a group label and a field label are German
    assert "Konfiguration — die DCS-Seite" in config
    assert "Identität &amp; Zugriff" in config and "Servername" in config

 # the Missions tab's heading is German (its empty sentence here is the ACTION's own, out of scope)
    assert ">Missionen</h3>" in missions
    assert 'class="tag">Rotationsliste</span>' in missions or "nur Lesen" in missions


# --------------------------------------------------------------------------- the dialog renders

@pytest.mark.asyncio
async def test_a_write_dialog_renders_in_german_and_leaves_the_target_verbatim(
        config_dir, clean_registry, recording_bot):
    """The rendered check: with German chosen, a write DIALOG is German — its title pattern, its
    buttons, its option and its warning — while the TARGET (the server name) stays exactly as it is.

    English stays the msgid, so the English dialog is byte-for-byte what it was (the write suites
    assert that); here the German is proved end to end on the console's ONE dialog component, whose
    copy the page modules compose (``pages/actions.confirm_context``).
    """
    from core import Status
    from test_webui_write_server_actions import CONFIRM, RESTART, SHUTDOWN, fleet, open_page, token_of

    app = build(config_dir, fleet(Status.RUNNING, players=3))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        restart = await http.post(RESTART + CONFIRM,
                                  data={"server": "SRS-1", "_csrf_token": token})
        shutdown = await http.post(SHUTDOWN + CONFIRM,
                                   data={"server": "SRS-1", "_csrf_token": token})

 # the TITLE PATTERN is German — the action's label is translated, the target name is untouched
    assert "Neustart SRS-1?" in restart.text
    assert "Restart SRS-1?" not in restart.text
 # the PRIMARY and CANCEL buttons are German
    assert "Server neu starten" in restart.text
    assert ">Abbrechen</a>" in restart.text and ">Cancel</a>" not in restart.text
 # the WARNING and the "what will happen" copy are German, their markup preserved
    assert "<b>Spieler, die gerade fliegen, verlieren ihren Einsatz.</b>" in restart.text
    assert "Ein Neustart stoppt die Mission" in restart.text
 # the target sentence is German and the TARGET itself is exactly the server name
    assert 'Ziel: <span class="target">SRS-1</span>' in restart.text
    assert "SRS-1" in restart.text

 # the OPTION's label and its help line are German on the shutdown dialog (the box is a form field
 # drawn on the dialog), and the server name is never translated
    assert "Wartung setzen, damit der Scheduler ihn nicht neu startet" in shutdown.text
    assert "Standardmäßig aktiviert" in shutdown.text


# ---------------------------------------------------- the Logs page renders (card F1)

@pytest.mark.asyncio
async def test_the_logs_page_and_its_shared_log_card_render_in_german(tmp_path):
    """Card F1: with German chosen, the Logs page's lead and the (shared) log card's chrome are German.

    The log card is the SAME partial the dashboard renders, so wrapping it fixes BOTH surfaces; the
    Logs page's own heading and lead come from the module's constants, and the dashboard renders the
    card's placement sentence (the Logs page omits it — its links list is empty). The English render
    is unchanged (other suites assert it); here the German is proved end to end through the real app.
    """
    from services.webservice.pages import logs as logs_page
    from test_webui_dashboard import BOT_YAML, _build, _hash, _login, _source, node, server

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    log_file = tmp_path / "bot.log"
    log_file.write_text("2026-09-27 10:00:00.000 INFO\tCache warm\n", encoding="utf-8")
    app, _registrar = _build(tmp_path, users=[
        {"username": "specialk", "password_hash": _hash(), "roles": list(logs_page.LOGS_ROLES)}])
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={}), log_path=log_file)

    async with asgi_client(app) as client:
        await _login(client, "specialk")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        logs = (await client.get(logs_page.LOGS_PATH)).text
        dashboard = (await client.get("/")).text

 # the page head: the heading and the lead constant are German
    assert "<h1>Protokolle</h1>" in logs, "the Logs heading is not German"
    assert "Das Bot-Protokoll, über die volle Breite" in logs, "the Logs lead is not German"
 # the shared log card's chrome is German
    assert "Aktuelles Protokoll" in logs, "the log card title is not German"
    assert "Folgt: neueste" in logs, "the log card's follow tag is not German"
    assert "Angezeigt: INFO und höher" in logs, "the shown-level sentence is not German"
 # the English is gone from those surfaces
    assert "Recent log" not in logs and "Follow: newest" not in logs
 # the placement sentence renders on the DASHBOARD (its links list is non-empty) and is German
    assert "Protokollposition <b>" in dashboard
    assert "längste Liste" in dashboard and "log position" not in dashboard


# ---------------------------------------------------- the interpolation guard (a malformed catalog)

class _DictTrans(gettext.NullTranslations):
    """A translations object backed by a plain dict — the shape the guard is probed with.

    Unknown msgids fall through to the msgid (English), exactly like ``NullTranslations``; only the
    keys the test supplies are "translated".
    """

    def __init__(self, mapping: dict[str, str]):
        super().__init__()
        self.mapping = mapping

    def gettext(self, message: str) -> str:
        return self.mapping.get(message, message)


@pytest.mark.asyncio
async def test_a_translation_with_a_stray_placeholder_degrades_to_english(
        config_dir, clean_registry, recording_bot, monkeypatch):
    """BLOCKER: a malformed ``msgstr`` renders the ENGLISH sentence — it never 500s the dialog.

    ``confirm.html`` interpolated every sentence record with a bare ``.format(**params)``: a
    translated msgstr carrying a placeholder the params do not supply — a stray ``{extra}``, a
    positional ``{0}`` — raised ``KeyError``/``IndexError`` and took the WHOLE dialog down with a 500,
    for every write in the console. The ONE shared guard (``templating.SENTENCE_GLOBAL``) contains
    that: the translation is tried, and on an interpolation error the UNTRANSLATED English sentence is
    rendered instead. This drives a REAL RESTART dialog through the real app with a catalog that fakes
    BOTH failures — a ``KeyError`` on the warning, an ``IndexError`` on the button's title — and
    asserts the page still renders, in English, with the target name interpolated.
    """
    from core import Status
    from test_webui_write_server_actions import CONFIRM, RESTART, fleet, open_page, token_of

    warning = ("<b>Players currently flying lose their sortie.</b> The mission restarts from the "
               "beginning — mid-mission progress is gone.")
    go_title = "Restart {server} — players flying lose their sortie"
    bad = _DictTrans({
 # a placeholder the params do not supply -> KeyError('extra')
        warning: "<b>Spieler verlieren ihren Einsatz.</b> {extra}",
 # a positional placeholder -> IndexError (server is a keyword, so index 0 is out of range)
        go_title: "Neustart {server} {0}",
    })
    monkeypatch.setattr(i18n, "translations_for", lambda language: bad)

    app = build(config_dir, fleet(Status.RUNNING, players=3))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        page = await http.post(RESTART + CONFIRM,
                               data={"server": "SRS-1", "_csrf_token": token})

    assert page.status_code == 200, "a bad catalog line took the whole dialog down"
    body = page.text
 # the warning degraded to the English sentence, complete — not to a 500 and not half-written
    assert "<b>Players currently flying lose their sortie.</b>" in body
    assert "{extra}" not in body and "Spieler verlieren ihren Einsatz" not in body
 # the button's title degraded too (the IndexError), with the server name interpolated
    assert 'title="Restart SRS-1 — players flying lose their sortie"' in body
    assert "{0}" not in body


# ---------------------------------------------------- the served message map (card I5)

def test_the_served_message_map_exists_per_language(shell_app):
    """The console's JavaScript strings are SERVED, per language — the SAME ``webui`` catalog.

    English is the msgid (the base language's map is the English strings); German is the draft. The
    map covers EXACTLY the keys the scripts ask for (:data:`i18n.JS_MESSAGES`), in both languages, so
    a script can never look up a key that renders nowhere.
    """
    app, _ = shell_app
    english = i18n.messages_for(_request(app, language="en"))
    german = i18n.messages_for(_request(app, language="de"))
    assert set(english) == set(i18n.JS_MESSAGES) == set(german)
    assert english["log.load_older"] == "Load older"
    assert german["log.load_older"] == "Ältere laden"           # German, from the draft catalog
    assert english["reveal.show"] == "Show password"
    assert german["reveal.show"] == "Passwort anzeigen"
    assert german["live.status"] == "Live-Updates: {status}"    # the {status} hole is preserved
 # the placeholders the JS swaps in are preserved by the translation (a value the server knew)
    assert "{seconds}" in german["live.not_ready"]
    assert "{n}" in german["mission.remove_many"] and "{total}" in german["mission.count"]


@pytest.mark.asyncio
async def test_the_message_route_serves_the_request_language_and_is_gated(shell_app):
    """The map is a real route: it follows the request's language and the console's access gate.

    It is a rendering path like every page, so it declares ``dashboard.view`` — an anonymous caller is
    refused, exactly as for a page; the signed-in caller gets the map in their language, uncached
    (``no-store``: a language-shaped body must never sit in a shared cache).
    """
    app, _ = shell_app

    async with asgi_client(app) as client:
        anonymous = await client.get(i18n.MESSAGES_PATH, follow_redirects=False)
    assert anonymous.status_code in (302, 303, 307, 403), "the message map is not gated"

    stub_identity({"Admin"})
    async with asgi_client(app) as client:
        client.cookies.set(i18n.COOKIE_NAME, "de")
        german = await client.get(i18n.MESSAGES_PATH)
        client.cookies.set(i18n.COOKIE_NAME, "en")
        english = await client.get(i18n.MESSAGES_PATH)

    assert german.status_code == 200 and english.status_code == 200
    assert german.headers["content-type"].startswith("application/javascript")
    assert "no-store" in german.headers.get("cache-control", "")
    assert "window.DCSSB_MESSAGES" in german.text and "window.dcssbMsg" in german.text
    assert '"log.load_older": "Ältere laden"' in german.text
    assert '"log.load_older": "Load older"' in english.text
    assert "Ältere laden" not in english.text


def test_the_console_javascript_carries_no_translation_logic():
    """NO translation lives in the JS: the scripts look a key up and swap, nothing more.

    Every user-visible string is served (``/i18n/messages.js``); the shipped scripts must therefore
    contain no gettext call, no ``_()`` marker and no language switch — only the ``window.dcssbMsg``
    lookup. ``base.html`` includes the map BEFORE the scripts that read it.
    """
    static = REPO / "services/webservice/static"
    scripts = ("shell.js", "server-log.js", "mission-select.js", "reveal.js", "server-status.js",
               "submit.js", "filepick.js")
    for name in scripts:
        text = (static / name).read_text("utf-8")
 # no translation mechanism in the JS
        for banned in ("gettext", "ngettext", "_(", "n_(", "Accept-Language", "dcssb_lang"):
 # the doc comments legitimately mention i18n; assert on CODE, not prose, by checking a
 # gettext CALL shape rather than the bare word
            if banned in ("gettext", "ngettext"):
                assert "gettext(" not in text, f"{name} calls gettext"
            else:
                assert banned not in text, f"{name} carries '{banned}'"
 # the scripts that DO show text read the served map
    for name in ("shell.js", "server-log.js", "mission-select.js", "reveal.js"):
        assert "dcssbMsg" in (static / name).read_text("utf-8"), f"{name} does not read the map"
 # the shell includes the map, and before the interceptor it must precede
    base = (REPO / "services/webservice/templates/base.html").read_text("utf-8")
    assert 'src="/i18n/messages.js"' in base
    assert base.index('src="/i18n/messages.js"') < base.index('src="/static/submit.js"')


@pytest.mark.asyncio
async def test_a_row_tooltip_and_the_live_status_line_render_in_german(
        config_dir, clean_registry, recording_bot):
    """The rendered check the card names: a row tooltip and the live-status line are German.

    Driven through the REAL app (the write suites' server fleet), so the tooltip is the one a strip
    glyph actually wears: the restart control's ``title``/``aria-label`` are the German draft, and the
    dashboard's live-status placeholder is German too (the served map then swaps the live word).
    """
    from core import Status
    from test_webui_write_server_actions import fleet, open_page

    app = build(config_dir, fleet(Status.RUNNING, players=3))
    async with client(app) as http:
        await sign_in(http, "mod1")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        servers = await open_page(http)
        dashboard = await open_page(http, "/")

 # the row tooltip and the accessible name on the RESTART glyph are German
    assert 'title="Neustart — fliegende Spieler verlieren ihren Einsatz"' in servers
    assert 'aria-label="Server SRS-1 neu starten"' in servers
    assert "Restart — players currently flying lose their sortie" not in servers
 # the live-status placeholder the JS overwrites is German
    assert "Live-Updates: nicht verfügbar" in dashboard
    assert "Live updates: unavailable" not in dashboard


@pytest.mark.asyncio
async def test_the_last_chrome_renders_in_german_and_russian(config_dir, clean_registry, tmp_path):
    """Card I9: the last unwrapped visible chrome renders in the REQUEST's language.

    Three spots, each driven through the REAL app (the per-language Jinja environment), not a unit
    stub: the notice's audit warning (``{% trans %}``), the mission picker's field label and help line
    (their SOURCE was unmarked, so the extractor never saw them), and the log-window endpoint's three
    JSON messages — which the server renders and the JavaScript only swaps into the DOM (it carries no
    translation logic). German (informal Du) and Russian (informal ты) are the two languages the card
    names; Chinese does not exist yet and picks these up in the next card.
    """
    from services.webservice.pages import actions as actions_page
    from test_webui_missions_tab import configured_fleet, token_of
    from test_webui_server_log_tab import MASTER, WINDOW, WindowNode, admin_app
    from test_webui_write_pause import act, fleet, open_page

 # 1. the notice's audit-warning sentence — a pause with no bot to confirm the trail says so.
    for language, sentence in {
        "de": "Der Audit-Eintrag für diese Aktion konnte nicht geschrieben werden — schau ins Bot-Log.",
        "ru": "Запись аудита для этого действия не удалось сохранить — смотри журнал бота.",
    }.items():
        app = build(config_dir, fleet())
        async with client(app) as http:
            await sign_in(http, "mod1")
            http.cookies.set(i18n.COOKIE_NAME, language)
            reply = await act(http, actions_page.PAUSE_PATH, "SRS-1")
            assert reply.status_code == 303
            after = await open_page(http)
        assert sentence in after, language

 # 2. the mission picker's label and help line, on the ADD dialog.
    for language, (label, help_text) in {
        "de": ("Bereits auf diesem Server vorhandene Missionsdatei",
               "Es werden nur Dateien angeboten, die NOCH NICHT in der Rotation sind."),
        "ru": ("Файл миссии, уже имеющийся на этом сервере",
               "Предлагаются только файлы, которых ЕЩЁ НЕТ в ротации."),
    }.items():
        app = build(config_dir, configured_fleet(), plugins=("mission",))
        async with client(app) as http:
            await sign_in(http, "specialk")
            http.cookies.set(i18n.COOKIE_NAME, language)
            page = await http.get("/fleet/SRS-1?tab=missions")
            dialog = await http.post(f"{actions_page.MISSIONS_ADD_PATH}{actions_page.CONFIRM_SUFFIX}",
                                     data={"server": "SRS-1", "_csrf_token": token_of(page.text)})
        assert dialog.status_code == 200, dialog.text
        assert f'<label for="path-dialog">{label}</label>' in dialog.text, language
        assert f'id="path-dialog-help">{help_text}</span>' in dialog.text, language

 # 3. the log-window JSON messages — rendered by the SERVER into the body the follower shows.
    for language, (needs_position, bad_offset, bad_behind) in {
        "de": ("Ein Offset oder eine Behind-Position ist erforderlich.",
               "Der Offset muss eine nicht-negative ganze Zahl sein.",
               "Die Behind-Position muss eine nicht-negative ganze Zahl sein."),
        "ru": ("Требуется offset или позиция behind.",
               "Offset должен быть неотрицательным целым числом.",
               "Позиция behind должна быть неотрицательным целым числом."),
    }.items():
        app = admin_app(config_dir, tmp_path, node=WindowNode(MASTER))
        async with client(app) as http:
            await sign_in(http, "specialk")
            http.cookies.set(i18n.COOKIE_NAME, language)
            none = await http.get(WINDOW)
            offset = await http.get(WINDOW + "?offset=abc")
            behind = await http.get(WINDOW + "?behind=abc")
        assert none.status_code == offset.status_code == behind.status_code == 400
        assert none.json()["message"] == needs_position, language
        assert offset.json()["message"] == bad_offset, language
        assert behind.json()["message"] == bad_behind, language

 # 3b. the endpoint's OTHER messages — the node name / path are interpolated AFTER the sentence is
 # translated, so they stay verbatim. The tab's INITIAL note is the same sentence class and agrees.
    offline_de = "Knoten 'dcs-a-01' ist offline oder unbekannt, daher kann sein Log nicht aus dieser " \
                 "Konsole gelesen werden."
    offline_ru = "Узел 'dcs-a-01' отключён или неизвестен, поэтому его лог нельзя прочитать из этой консоли."
    for language, offline in {"de": offline_de, "ru": offline_ru}.items():
        app = admin_app(config_dir, tmp_path, offline=True)
        async with client(app) as http:
            await sign_in(http, "specialk")
            http.cookies.set(i18n.COOKIE_NAME, language)
            window = await http.get(WINDOW + "?offset=0")
            page = await http.get(f"/fleet/SRS-1?tab=log")
        assert offline in window.json()["message"], language
        assert offline.replace("'", "&#39;") in page.text, language

 # 3c. no log file yet (pending): the path and node are operator data, kept verbatim inside the
 # translated sentence.
    app = admin_app(config_dir, tmp_path, node=WindowNode(MASTER))
    async with client(app) as http:
        await sign_in(http, "specialk")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        pending = await http.get(WINDOW + "?offset=0")
    assert pending.json()["pending"] is True
    assert pending.json()["message"].startswith("Noch keine Logdatei für diesen Server (gesucht unter ")
    assert "dcs.log' auf dem Knoten 'dcs-a-01')" in pending.json()["message"]


# ---------------------------------------------------- the config note + the 403 notice (card I10)

@pytest.mark.asyncio
async def test_the_config_apply_note_and_the_403_notice_render_in_german_and_russian(
        config_dir, clean_registry, recording_bot, tmp_path):
    """Card I10: the Config tab's apply note, and the console's OWN 403 refusal in the one-shot notice.

    Two spots, both driven through the REAL app's per-language Jinja pipeline (not a unit stub):

 * the Config tab prints the stop-first APPLY note (``config.apply_note``) for a server that is not
      STOPPED/SHUTDOWN. Its SOURCE was unmarked (so the extractor never saw it) and its render was not
      wired through ``_`` — both are fixed, so it renders in the request's language;
 * a write the caller may not make is refused with the console's OWN 403, and the refusal is
      SURFACED INTO THE PAGE by the ONE-SHOT NOTICE (``_notice.html``, the target ``shell.js`` swaps
      in). The server renders the sentence for the request's language; the JavaScript only swaps it.
      The ACTION's own message is a different thing and renders verbatim (it is composed where the
      action runs and shown on Discord too), which the last assertion pins with a SUCCESSFUL write.
    """
    from services.webservice.pages import actions as actions_page
    from test_webui_dashboard import (BOT_YAML, Status, _build, _hash, _login, _source, node, player,
                                      server)
    from test_webui_w4m_modal_and_pulse import notice_fragment
    from test_webui_write_pause import act, fleet, open_page, token_of

 # 1. the Config tab's apply note (the server is RUNNING, so the change cannot apply yet).
    for language, sentence in {
        "de": "Der Server muss gestoppt sein, um seine Konfiguration zu ändern. Stoppe ihn auf der "
              "Servers-Seite und bearbeite sie dann hier.",
        "ru": "Сервер должен быть остановлен, чтобы изменить его конфигурацию. Останови его на "
              "странице Servers и измени её здесь.",
    }.items():
        (tmp_path / "services").mkdir(exist_ok=True)
        (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
        provider = lambda: _source(  # noqa: E731 - the source seam, as the other pages use it
            servers=[server(name="Alpha", status=Status.RUNNING, players=[player("Viper-1")])],
            node_obj=node(instances={}))
        app, _registrar = _build(tmp_path, users=[
            {"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}])
        app.state.webui_source_provider = provider
        async with asgi_client(app) as http:
            await _login(http, "staff")
            http.cookies.set(i18n.COOKIE_NAME, language)
            config = (await http.get("/fleet/Alpha?tab=configuration")).text
        assert sentence in config, language
        assert "The server must be stopped to change its configuration." not in config, language

 # 2. an out-of-scope write: the console's own 403, surfaced into the page by the one-shot notice.
    for language, refusal in {
        "de": "Nicht autorisiert (dieses Ziel liegt außerhalb deines Bereichs).",
        "ru": "Не авторизовано (эта цель вне твоей области).",
    }.items():
        app = build(config_dir, fleet())
        async with client(app) as http:
            await sign_in(http, "hoster-b")            # manages Host B
            page = await open_page(http)
            reply = await http.post(actions_page.PAUSE_PATH,
                                    data={"server": "SRS-1", "_csrf_token": token_of(page)},
                                    follow_redirects=False)
            http.cookies.set(i18n.COOKIE_NAME, language)
            notice = await notice_fragment(http)
        assert reply.status_code == 403
        assert notice, language
        assert refusal in notice, language
        assert "Not authorized (this target is outside your scope)." not in notice, language
        assert 'class="notice bad"' in notice and "notice-msg" in notice

 # 2b. the ACTION's own message is NOT looked up in a catalog: an accepted write's notice renders
 # the action's own English sentence verbatim even with German chosen.
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")                  # manages Host A (owns SRS-1)
        page = await open_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        accepted = await act(http, actions_page.PAUSE_PATH, "SRS-1")
        assert accepted.status_code == 303
        notice = await notice_fragment(http)
    assert notice and 'class="notice ok"' in notice, "an accepted write leaves an ok notice"


# ---------------------------------------------------- the i18n tail (card I11)

@pytest.mark.asyncio
async def test_the_tab_tooltip_renders_in_german_and_russian(tmp_path):
    """Card I11: a tab's problem TOOLTIP is chrome and renders in the request's language.

    It is a sentence RECORD (``{"msgid", "params"}``) rendered at RENDER time through the template's
    guarded ``say``, so the count is interpolated in the reader's language. Driven through the REAL
    app (the dashboard's own source seam); English stays the msgid, so its render is byte-identical.
    """
    from test_webui_dashboard import BOT_YAML, Status, _build, _hash, _login, _source, node, server

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    app, _registrar = _build(tmp_path, users=[
        {"username": "staff", "password_hash": _hash(), "roles": ["DCS Admin"]}])
    app.state.webui_source_provider = lambda: _source(
        servers=[server(name="Down", status=Status.SHUTDOWN)],
        nodes={"dcs-master-01": node(instances={}), "dcs-agent-02": None},
        node_obj=node(instances={}))

    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        german = (await client.get("/")).text
        client.cookies.set(i18n.COOKIE_NAME, "ru")
        russian = (await client.get("/")).text
        client.cookies.set(i18n.COOKIE_NAME, "en")
        english = (await client.get("/")).text

 # the Nodes and Servers tabs each wear the problem sentence, in the reader's language
    assert 'title="1 Knoten offline"' in german, german
    assert 'title="1 Server nicht in Betrieb"' in german
    assert 'title="1 узлов офлайн"' in russian
    assert 'title="1 серверов не запущено"' in russian
 # English is the msgid: byte-for-byte the string it always was
    assert 'title="1 node(s) offline"' in english and 'title="1 server(s) not running"' in english
    assert "node(s) offline" not in german and "Knoten offline" not in english


@pytest.mark.asyncio
async def test_the_shell_refusal_page_renders_in_german_and_russian(shell_app):
    """Card I11: the shell's standalone 403 document is translated AT RENDER.

    ``shell.refusal_page`` composes the whole page in Python (no template), so its copy is translated
    through the request's per-language environment. Driven through the REAL app: the environment's
    translations object follows the request's cookie, and the ``lang`` attribute follows the code.
    """
    from services.webservice import auth as auth_layer
    from test_webui_refusal import HTML, _StubManager

    app, _registrar = shell_app
    stub_identity(["GameMaster"])
    app.state.webui_auth = _StubManager(auth_layer.Identity(subject="nobody", backend="local",
                                                            roles=frozenset({"GameMaster"})))
    async with asgi_client(app) as client:
        client.cookies.set(i18n.COOKIE_NAME, "de")
        german = await client.get("/", headers=HTML)
        client.cookies.set(i18n.COOKIE_NAME, "ru")
        russian = await client.get("/", headers=HTML)

    assert german.status_code == russian.status_code == 403
    assert '<html lang="de">' in german.text and "<h1>Zugriff verweigert</h1>" in german.text
    assert "Zurück zur Übersicht" in german.text
    assert '<html lang="ru">' in russian.text and "<h1>Доступ запрещён</h1>" in russian.text
    assert "Вернуться к обзору" in russian.text


@pytest.mark.asyncio
async def test_the_shell_retryable_page_renders_in_german_and_russian():
    """Card I11: the shell's standalone 503 document is translated AT RENDER, JS copy included.

    ``shell.retryable_page`` composes the whole page — HTML and the two JS status lines — in Python,
    so every piece of its copy renders in the request's language. Driven through the REAL app; the
    English render (no language cookie) is byte-identical, JS ``stop("…")`` lines and all.
    """
    from services.webservice import permissions
    from test_webui_retryable_page import _client, _refusal_app

    app = _refusal_app()
    async with _client(app) as client:
        client.cookies.set(i18n.COOKIE_NAME, "de")
        german = await client.get("/probe/admin", headers={"accept": "text/html"})
        client.cookies.set(i18n.COOKIE_NAME, "ru")
        russian = await client.get("/probe/admin", headers={"accept": "text/html"})
        client.cookies.set(i18n.COOKIE_NAME, "en")
        english = await client.get("/probe/admin", headers={"accept": "text/html"})

    for response in (german, russian, english):
        assert response.status_code == permissions.RETRYABLE_STATUS
    assert '<html lang="de">' in german.text and "<h1>Die Konsole startet gerade</h1>" in german.text
    assert "Jetzt erneut versuchen" in german.text
    assert 'stop("Noch nicht bereit. Versuch es von Hand erneut, wenn du bereit bist.");' in german.text
    assert '<html lang="ru">' in russian.text and "<h1>Консоль запускается</h1>" in russian.text
    assert "Повторить сейчас" in russian.text
    assert 'stop("Ещё не готово. Повтори вручную, когда будешь готов.");' in russian.text
 # English is the msgid: the document is byte-for-byte what it always was
    assert '<html lang="en">' in english.text
    assert "<h1>The console is starting up</h1>" in english.text and "Retry now" in english.text
    assert 'stop("Still not ready. Retry by hand when you are ready.");' in english.text
    assert "Die Konsole startet gerade" not in english.text


# --------------------------------------------------------------------------- helpers

def _request(app, *, language: str | None = None, header: str | None = None,
             path: str = "/", query: str = ""):
    """A request-shaped stub for the i18n functions (they read only cookies/headers/url/state)."""
    request = types.SimpleNamespace()
    request.app = app
    request.cookies = {i18n.COOKIE_NAME: language} if language else {}
    request.headers = {"accept-language": header} if header else {}
    request.url = types.SimpleNamespace(path=path, query=query)
    return request
