"""The public site's theme sheets: the shipped presets, the uploaded sheet, the expert block, one
composer, one answer.

WHAT A THEME SHEET IS. A preset or an expert block is a TOKEN SHEET: custom-property declarations
and nothing else. ``site.css`` declares every colour, family and length as a custom property and no
component rule reads a literal, so a sheet loaded AFTER it wins at equal specificity by source order
— that is the whole theme mechanism. A token sheet therefore never carries a selector and never a
component rule.

THREE SPELLINGS OF ONE INTENT. A site names its palette with ``theme.preset`` (one of the sheets
shipped under ``static/site-presets/``), ``theme.sheet`` (a ``.css`` file the operator uploaded into
the site's asset folder) or ``theme.expert`` (the body of one ``:root`` rule, stored in the site
file). AT MOST ONE may be set: :func:`compose` refuses two, because two sources make "what is on the
page" unknowable.

THE PRESETS ARE FILES, NOT A SECOND STYLESHEET SYSTEM. A preset IS a token sheet — the same
grammar as the expert block, the same ``:root``-only rule, the same route. Anything a preset does,
an expert block does; :data:`PRESETS` only says which files ship, in what order, under which label
and with which colour scheme.

AN UPLOADED SHEET IS SERVED AS WRITTEN. It is the operator's own CSS, answered byte for byte: no
validation, no wrapping, no rewriting. So it may carry ``@font-face``, ``url()`` and component rules,
and the visitor's browser loads whatever it names from this installation's own origin. The expert
block is the validated path; the upload is the escape hatch for a sheet that already exists.

WHAT THE VALIDATOR PREVENTS. The expert text is served from the installation's own origin as CSS,
so it is code-adjacent content. Two things keep it to values: the server wraps the text in
``:root { ... }`` (a selector cannot be expressed at all — the text is never in selector position),
and :func:`validate` refuses everything the wrapper cannot: a misplaced declaration, a fetch
(``url(``), an import or any other at-rule, a legacy script vector, ``!important``, and the
characters that would close the rule the wrapper opened.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from .sites import MAX_EXPERT_LENGTH, MAX_SHEET_LENGTH, sheet_path

__all__ = [
    "CSS_MEDIA_TYPE",
    "DEFAULT_PRESET",
    "MAX_EXPERT_LENGTH",
    "MAX_SHEET_LENGTH",
    "PRESETS",
    "PRESET_BY_ID",
    "PRESET_DIR",
    "PRESET_IDS",
    "Preset",
    "Refusal",
    "compose",
    "expert_rule",
    "preset_sheet",
    "response",
    "scheme",
    "sheet_text",
    "validate",
]

log = logging.getLogger(__name__)

#: the type a theme sheet is answered as, never negotiated: a token sheet is UTF-8 text by contract.
CSS_MEDIA_TYPE = "text/css; charset=utf-8"

#: the folder the shipped sheets live in. A preset is read from HERE and nowhere else — no request
#: ever names a path, so the answer can only ever be one of these four files.
PRESET_DIR: Path = Path(__file__).with_name("static") / "site-presets"


@dataclass(frozen=True, slots=True)
class Preset:
    """One shipped token sheet: the id a site names, its label, its scheme and its file."""

    #: the value ``theme.preset`` carries
    id: str
    #: the gallery's label. The name of a sheet, carried VERBATIM: it is the palette's own name, so
    #: a translated one would name something the sheet does not.
    label: str
    #: ``light`` or ``dark`` — the ``color-scheme`` the sheet's palette needs. A sheet may only
    #: re-declare custom properties and ``color-scheme`` is not one, so the page's ``data-theme``
    #: is set from this value instead (see ``static/site.css``'s ``html[data-theme="light"]``).
    scheme: str
    #: the file's name inside :data:`PRESET_DIR`
    filename: str


#: the four shipped presets, in the order the gallery draws them. CLOSED: this tuple is the only
#: registry, and one test asserts every id has a file and every file has an id.
PRESETS: tuple[Preset, ...] = (
    Preset(id="brass", label="Brass", scheme="dark", filename="brass.css"),
    Preset(id="slate", label="Slate", scheme="light", filename="slate.css"),
    Preset(id="midnight", label="Midnight", scheme="dark", filename="midnight.css"),
    Preset(id="parchment", label="Parchment", scheme="light", filename="parchment.css"),
)

#: the preset ids, in gallery order.
PRESET_IDS: tuple[str, ...] = tuple(preset.id for preset in PRESETS)

#: preset id -> its sheet.
PRESET_BY_ID: dict[str, Preset] = {preset.id: preset for preset in PRESETS}

#: the palette a site that names none is served: the shipped look, restated as a sheet.
DEFAULT_PRESET = "brass"


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a block was refused: which line, where on it, and the sentence naming the reason."""

    line: int | None
    column: int | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        """The verdict shape the settings page's live check answers."""
        return {"ok": False, "line": self.line, "column": self.column, "message": self.message}


# ----------------------------------------------------------------------------- the validator

#: one declaration: a custom property's name, a colon, a value, a closing semicolon.
_DECLARATION = re.compile(r"^(--[a-z0-9-]{1,64})\s*:\s*(.*?)\s*;$")

#: the shapes a token value may never contain, in the order they are reported.
_SCRIPT_VECTORS: tuple[tuple[str, str], ...] = (
    ("expression(", "expression() is not accepted: a value is a value, never a script"),
    ("javascript:", "javascript: is not accepted: a value is a value, never a script"),
    ("-moz-binding", "-moz-binding is not accepted: a legacy script vector"),
    ("behavior:", "behavior: is not accepted: a legacy script vector"),
)

#: characters that close the rule the server wraps the text in, or that only make sense in a
#: document: a brace, a backslash, an angle bracket, a comment marker.
_FORBIDDEN_CHARS: tuple[str, ...] = ("{", "}", "\\", "<", ">", "/*", "*/")


def _line_refusal(number: int, reason: str, column: int | None = None) -> Refusal:
    return Refusal(line=number, column=column, message=f"Line {number} — {reason}.")


def validate(text: Any) -> Refusal | None:
    """*text* as an expert block: ``None`` when accepted, the :class:`Refusal` naming the reason.

    The accepted grammar is one declaration per non-blank line — ``--name: value;`` — and nothing
    else. Each refusal names its line and says what was seen, because the operator's editor marks
    that line and the settings page prints the sentence: a generic answer could not be acted on.

    The bound is :data:`MAX_EXPERT_LENGTH` characters. The text travels YAML into a form into a
    page, so the bound keeps all three honest.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        return Refusal(line=None, column=None, message="The block must be text.")
    if len(text) > MAX_EXPERT_LENGTH:
        return Refusal(line=None, column=None,
                       message=f"The block is {len(text)} characters; at most "
                               f"{MAX_EXPERT_LENGTH} are accepted.")
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if "@" in line:
            return _line_refusal(number,
                                 "an at-rule is not accepted: a sheet declares properties, it "
                                 "imports nothing")
        if not line.startswith("--"):
            if "{" in line or "}" in line:
                return _line_refusal(number,
                                     "a component selector is not accepted: only custom "
                                     "properties are")
            return _line_refusal(number,
                                 "only a custom property may be declared here, and its name "
                                 "starts with '--'")
        if "url(" in lowered:
            return _line_refusal(number,
                                 "url() is not accepted: a sheet declares values, it fetches no "
                                 "files")
        for needle, reason in _SCRIPT_VECTORS:
            if needle in lowered:
                return _line_refusal(number, reason)
        if "!important" in lowered:
            return _line_refusal(number,
                                 "!important is not accepted: a token sheet never needs one, and "
                                 "it would win over the palette the site promised")
        for needle in _FORBIDDEN_CHARS:
            if needle in line:
                if needle in ("<", ">"):
                    return _line_refusal(number,
                                         "'<' and '>' are not accepted: a value is a value, never "
                                         "markup")
                return _line_refusal(number,
                                     f"'{needle}' is not accepted: it would close or escape the "
                                     f"rule this text is wrapped in")
        match = _DECLARATION.match(line)
        if match is None:
            if ":" not in line:
                return _line_refusal(number, "a declaration reads '--name: value;'")
            if not line.endswith(";"):
                return _line_refusal(number, "a declaration ends with ';'")
            return _line_refusal(number,
                                 "a token name matches '--[a-z0-9-]': lowercase letters, digits "
                                 "and hyphens, at most 64 of them")
        if ";" in match.group(2):
            return _line_refusal(number,
                                 "one declaration per line: a second ';' belongs on its own line")
        if not match.group(2).strip():
            return _line_refusal(number, "a declaration carries a value")
    return None


# ----------------------------------------------------------------------------- the composer

def preset_sheet(preset_id: str | None) -> str:
    """The shipped sheet of *preset_id*, or the default palette's when the id names none.

    An id this build does not ship answers the default sheet: a value that was valid when it was
    written must not take a public page down because a release dropped it.
    """
    preset = PRESET_BY_ID.get(str(preset_id or ""))
    if preset is None:
        if preset_id:
            log.warning("Theme: '%s' is not a preset this build ships (%s); serving '%s'.",
                        preset_id, ", ".join(PRESET_IDS), DEFAULT_PRESET)
        preset = PRESET_BY_ID[DEFAULT_PRESET]
    return (PRESET_DIR / preset.filename).read_text(encoding="utf-8")


def expert_rule(text: str) -> str:
    """*text* as ONE ``:root`` rule: the wrapper that makes a selector inexpressible.

    A blank line is dropped (the block is a list of declarations, and a blank one carries nothing)
    and every surviving declaration is indented by its two spaces.
    """
    body = "\n".join(f"  {line.strip()}" for line in str(text).splitlines() if line.strip())
    return f":root {{\n{body}\n}}\n"


def sheet_text(theme: Any, config_dir) -> str:
    """The uploaded sheet's own text, read through :func:`services.webservice.sites.sheet_path`.

    Raises ``ValueError``/``OSError``/``UnicodeDecodeError`` — the caller decides what a sheet it
    cannot read means. Nothing is decoded leniently: a file that is not UTF-8 is not the sheet the
    operator wrote, and answering replacement characters would serve a palette nobody chose.
    """
    if not config_dir:
        raise ValueError("the config directory is needed to read an uploaded sheet")
    return sheet_path(theme.sheet, config_dir).read_text(encoding="utf-8")


def compose(theme: Any, config_dir=None) -> str:
    """The CSS body served at ``<site>/theme.css`` for one site's ``Theme``.

    EXACTLY ONE SOURCE, in this order of resolution: the uploaded ``sheet`` (its file, served
    verbatim), the ``expert`` block (validated, then wrapped as one ``:root`` rule), or the
    ``preset`` sheet. A site that names none is served the default palette — the shipped look
    restated, so the answer is a sheet like any other.

    More than one source is a refusal, not a priority: the model already refuses it at parse, and
    answering one of them here would make the page and the file disagree without saying so.

    A source that cannot be served — a sheet whose file is gone or is not UTF-8, an expert block that
    fails :func:`validate` — falls back to the default palette with one warning. A public page must
    not answer 500 over a config edit, and the operator's live check already reported the text.

    *config_dir* is the bot's configuration directory: the only place an uploaded sheet is looked
    for. Without it a declared sheet cannot be read, which falls back like any other unreadable one.
    """
    declared = [key for key in ("sheet", "expert", "preset") if getattr(theme, key, None)]
    if len(declared) > 1:
        raise ValueError(
            f"a theme carries at most one of sheet, expert and preset; this one sets "
            f"{' and '.join(declared)}")
    if not declared:
        return preset_sheet(DEFAULT_PRESET)

    source = declared[0]
    if source == "sheet":
        try:
            return sheet_text(theme, config_dir)
        except (OSError, ValueError, UnicodeDecodeError) as ex:
            log.warning("Theme: the uploaded sheet '%s' could not be read (%s); serving the "
                        "default palette.", getattr(theme, "sheet", None), ex)
            return preset_sheet(DEFAULT_PRESET)
    if source == "expert":
        refusal = validate(theme.expert)
        if refusal is not None:
            log.warning("Theme: the expert block was not served (%s); serving the default "
                        "palette.", refusal.message)
            return preset_sheet(DEFAULT_PRESET)
        return expert_rule(theme.expert)

    return preset_sheet(theme.preset)


def scheme(theme: Any) -> str:
    """``light`` or ``dark``: the palette *theme* actually serves, as the page's colour scheme.

    A preset answers its own scheme. An uploaded sheet is served as written and names no scheme of
    its own, so like an expert block it takes the deprecated ``theme.light`` flag; every source with
    no flag set is a dark page, because the shipped palette is dark and the page follows what it
    renders.
    """
    preset = PRESET_BY_ID.get(str(getattr(theme, "preset", None) or ""))
    if preset is not None:
        return preset.scheme
    return "light" if getattr(theme, "light", False) else "dark"


# ----------------------------------------------------------------------------- the answer

def _matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header names *etag* (``*`` names any current representation)."""
    if not header:
        return False
    for candidate in header.split(","):
        value = candidate.strip()
        if value == "*" or value == etag or value == f"W/{etag}":
            return True
    return False


def response(request: Request, text: str) -> Response:
    """*text* as the CSS answer for *request*: a strong ``ETag``, ``nosniff``, and ``304`` on a hit.

    The ``ETag`` is derived from the resolved BYTES, so it moves exactly when the sheet does: a
    preset swapped, an expert block re-saved. ``no-cache`` asks the browser to revalidate — the
    sheet is small and the answer is a 304 with no body when nothing changed.
    """
    body = text.encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    headers = {"ETag": etag,
               "Cache-Control": "no-cache",
               "X-Content-Type-Options": "nosniff"}
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type=CSS_MEDIA_TYPE, headers=headers)
