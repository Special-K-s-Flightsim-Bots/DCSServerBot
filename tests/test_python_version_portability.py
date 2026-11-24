"""No source file may use an f-string construct only Python 3.12+ can parse.

PEP 701 (3.12) allows an f-string to reuse its OWN quote inside ``{...}``::

    f'<a href="/">{_('Return to the dashboard')}</a>'      # SyntaxError on <= 3.11

The bot runs on the operator's interpreter, which may be older than the one this suite runs on (3.14
here), so that construct passes the whole suite and then kills the user's machine: it happened, in
``services/webservice/shell.py``, and the page module stopped importing entirely - the first SyntaxError
is the only one you see, and there were three.

``ast.parse(..., feature_version=(3, 11))`` does NOT catch it (checked: it parses such a snippet happily
as 3.11), so this test uses the tokenizer instead. On 3.12+ an f-string is tokenized as
FSTRING_START/MIDDLE/END and a nested literal inside the braces comes through as an ordinary STRING
token - so an inner STRING whose quote character equals the enclosing f-string's is exactly the
offending construct, wherever it appears in the file.

WHAT THIS DOES NOT COVER: a real 3.11 interpreter is the gold standard and this is not it. The two
checks here - the 3.11 grammar through ``ast``'s ``feature_version``, and the tokenizer for f-string
quoting - were chosen because they run everywhere with no second interpreter installed. (Installing one
was attempted and abandoned: the environment's consent gate stopped it, and that consent is not mine to
grant.) The f-string check is also quote-character based: an f-string reusing its own triple-quote is
caught the same way, but a construct invalid for some other reason entirely is not its business.
"""
from __future__ import annotations

import ast
import io
import textwrap
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".venv", "venv", "site-packages", ".git", "node_modules", "build", "dist"}


def nested_same_quote_fstrings(source: str) -> list[tuple[int, int, str]]:
    """(line, f-string start line, snippet) for each offending construct in *source*."""
    if not hasattr(tokenize, "FSTRING_START"):
        # Below 3.12 the interpreter ITSELF refuses the construct, so a failed parse is the finding —
        # and the answer is exact rather than heuristic. The token walk below is for the newer
        # interpreter this suite usually runs on, where the same source parses happily.
        try:
            # a control snippet is an indented fragment: dedent it, or an IndentationError would be
            # mistaken for the construct (``SyntaxError`` is its base class)
            ast.parse(textwrap.dedent(source))
        except SyntaxError as error:
            line = error.lineno or 1
            return [(line, line, (error.text or "").strip()[:40])]
        return []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    found: list[tuple[int, int, str]] = []
    open_fstrings: list[tuple[str, int]] = []
    for token in tokens:
        if token.type == tokenize.FSTRING_START:
            open_fstrings.append((token.string[-1], token.start[0]))
        elif token.type == tokenize.FSTRING_END:
            if open_fstrings:
                open_fstrings.pop()
        elif token.type == tokenize.STRING and open_fstrings:
            if token.string and token.string[0] == open_fstrings[-1][0]:
                found.append((token.start[0], open_fstrings[-1][1], token.string[:40]))
    return found


def _python_files():
    for path in sorted(REPO.rglob("*.py")):
        if SKIP_PARTS & set(path.parts):
            continue
        yield path


def test_the_detector_notices_the_construct():
    """Positive control: without this, a broken detector would look like a clean tree forever."""
    offending = "        f'<p><a href=\"/\">{_('Return to the dashboard')}</a></p>\\n'\n"
    assert nested_same_quote_fstrings(offending), "the detector failed to flag a known offender"

 #... and must stay quiet on the two legal neighbours, or it would just be noise.
    legal = (
        "        f\"<h1>{_('Access denied')}</h1>\\n\"\n"          # other quote for the nested literal
        "        f'<p class=\"x\">plain</p>\\n'\n"                  # quotes reused in the TEXT, not {}
        "        f'{value}'\n"                                     # no nested literal at all
    )
    assert not nested_same_quote_fstrings(legal), "the detector flags legal f-strings"


def test_every_file_parses_with_the_3_11_grammar():
    """The README promises Python 3.11 and above, so nothing here may need a newer grammar.

    ``feature_version`` makes ast parse with that version's rules and reject syntax a later version
    added (3.12 type parameters, 3.13 defaults, and so on). It does not model the f-string quoting
    change - ``test_no_f_string_reuses_its_own_quote_inside_braces`` covers that. Between them the two
    checks caught everything that was wrong here: one class, three lines, all in webservice/shell.py.
    """
    rejected = []
    for path in _python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))
        except SyntaxError as error:
            rejected.append(f"{path.relative_to(REPO)}:{error.lineno} {error.msg}")
    assert not rejected, ("these do not parse with a Python 3.11 grammar:\n  "
                          + "\n  ".join(rejected))


def test_no_f_string_reuses_its_own_quote_inside_braces():
    offenders = [
        (path.relative_to(REPO), line, start, snippet)
        for path in _python_files()
        for line, start, snippet in nested_same_quote_fstrings(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "these f-strings reuse their own quote inside {...}, which is a SyntaxError before Python 3.12 "
        "(use the other quote for the nested literal):\n"
        + "\n".join(f"  {path}:{line} (f-string opened at line {start}) {snippet}"
                    for path, line, start, snippet in offenders))
