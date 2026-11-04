"""Generate the SCHEMA prose catalogs (``locale/<lang>/LC_MESSAGES/schema.po`` + ``.mo``).

WHY A GENERATOR. A schema's ``desc:`` text is data the console renders (see
:mod:`services.webservice.configgen` for the fields, ``templating``'s ``schema_text`` global for the
lookup). Babel extracts msgids from templates and Python only — it never reads a YAML file — so those
descriptions cannot live in the ``webui`` catalog and cannot be maintained by hand in five files.
This module walks the schemas, turns every description into a msgid, merges the result into the
existing catalogs (KEEPING the translations already there) and compiles the ``.mo`` the runtime reads.

HOW TO RUN IT (from the repository root)::

    python3 -m services.webservice.schema_i18n            # update every language in locale/
    python3 -m services.webservice.schema_i18n --check     # report only; exit 1 when out of date
    python3 -m services.webservice.schema_i18n --langs de,es

The runtime needs no Babel: the console loads the generated ``.mo`` through the standard library's
``gettext``. Babel is a TOOLING dependency, needed here and in the test that pins this behaviour.

THE MERGE RULE (what makes it safe to re-run):

* the same description keeps its translation;
* a description whose TEXT changed at the SAME schema location is carried over and marked ``#, fuzzy``
  — a translator sees a candidate and reviews it, instead of silently losing it or silently keeping
  a translation of the old wording;
* a description that vanished from the schemas becomes obsolete (``#~``) rather than deleted, so a
  re-added field can still find its old translation;
* English is the source language: its catalog is filled in as the identity (``msgstr`` = ``msgid``),
  which is what the other languages fall back to.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCALE_DIR = REPO_ROOT / "locale"

#: the catalog domain shared with :data:`services.webservice.i18n.SCHEMA_DOMAIN` — imported lazily so
#: this tool can run (and be read) without the console's import graph.
SCHEMA_DOMAIN = "schema"

#: EVERY schema the console can render: the install's own (``schemas/``), and one folder per plugin,
#: service and extension. One rule, so a new extension's schema is picked up without touching this
#: file. ``.git``, caches and the vendored assets are skipped — they carry no schemas of ours.
SCHEMA_GLOBS = ("schemas/*.yaml", "*/schemas/*.yaml", "*/*/schemas/*.yaml")
SCHEMA_SKIP_PARTS = {".git", "__pycache__", "node_modules", ".pytest_cache", "vendor"}


def schema_files() -> list[pathlib.Path]:
    """Every schema file, in a stable order (repository-relative path)."""
    found: set[pathlib.Path] = set()
    for pattern in SCHEMA_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if not path.is_file() or path.suffix != ".yaml":
                continue
            if SCHEMA_SKIP_PARTS & set(path.relative_to(REPO_ROOT).parts):
                continue
            found.add(path)
    return sorted(found)


#: the schema's OWN keywords — never part of a field's path. The dotted path in the catalog's ``#.``
#: comment names the FIELD (``main_schema:database.pool_min``), which is what a translator looks up in
#: the schema; a path polluted with ``mapping``/``sequence`` would point at nothing they can search for.
SCHEMA_KEYWORDS = frozenset({
    "type", "nullable", "mapping", "sequence", "desc", "example", "include", "range", "enum",
    "func", "required", "default", "regex", "validate", "partial", "values", "choices",
})


def _descriptions(node, path: tuple, out: list) -> None:
    """Collect every ``desc:`` under *node*, with its dotted path and value line.

    Walks mappings AND sequence items: a description is legal on any rule — a leaf, a block, or one
    alternative inside a ``sequence:`` — and the console renders them all through the same field model.
    """
    if isinstance(node, dict):
        line = None
        loc = getattr(node, "lc", None)
        if loc is not None and "desc" in getattr(loc, "data", {}):
            line = loc.data["desc"][2] + 1          # ruamel: value's line, 0-based -> 1-based
        for key, value in node.items():
            if key == "desc":
                if isinstance(value, str) and value.strip():
                    out.append((path, line,
                                " ".join(value.split()) if "\n" in value else value))
                continue
            if key in SCHEMA_KEYWORDS:
                _descriptions(value, path, out)      # names a rule, never a field
            else:
                _descriptions(value, path + (str(key),), out)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _descriptions(item, path + (f"[{index}]",), out)


def collect() -> list[dict]:
    """Every description in the repository: ``{path, file, line, text}``, in schema-file order.

    The SAME text used in two places is ONE entry with two locations (that is what gettext is for);
    the first location wins the sort, so the catalog reads in schema order.
    """
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    entries: dict[str, dict] = {}
    for path in schema_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            document = yaml.load(path.read_text(encoding="utf-8"))
        except Exception as error:                      # a broken schema is the validator's business
            print(f"  ! skipped {rel}: {error}", file=sys.stderr)
            continue
        found: list = []
        _descriptions(document, (), found)
        for dotted, line, text in found:
            where = ".".join(dotted) or "<root>"
            entry = entries.get(text)
            if entry is None:
                entries[text] = {"text": text, "comments": [f"{path.stem}:{where}"],
                                 "locations": [(rel, line or 1)]}
            else:
                entry["comments"].append(f"{path.stem}:{where}")
                entry["locations"].append((rel, line or 1))
    return sorted(entries.values(), key=lambda item: item["locations"][0])


def catalogue(entries: list[dict], language: str):
    """A Babel catalog holding *entries*, with no translations (the 'new template' side of a merge)."""
    from babel.messages.catalog import Catalog

    catalog = Catalog(locale=language, domain=SCHEMA_DOMAIN)
    catalog.project = "DCSServerBot schema descriptions"
    catalog.version = ""
    for entry in entries:
        catalog.add(entry["text"], locations=entry["locations"],
                    auto_comments=sorted(set(entry["comments"])))
    return catalog


def merge(entries: list[dict], language: str):
    """The catalog to WRITE for *language*: the existing file's translations, updated.

    Returns ``(catalog, counts)``. ``counts`` reports what the run changed, per language, so the
    operator sees the effect of a schema edit instead of a silent rewrite.
    """
    from babel.messages.pofile import read_po

    fresh = catalogue(entries, language)
    path = LOCALE_DIR / language / "LC_MESSAGES" / f"{SCHEMA_DOMAIN}.po"
    if path.is_file():
        with path.open("rb") as handle:
            existing = read_po(handle, locale=language, domain=SCHEMA_DOMAIN)
        before = {m.id: (m.string or "", bool(m.fuzzy)) for m in existing if m.id}
        existing.update(fresh, keep_user_comments=True)
        catalog = existing
    else:
        before = {}
        catalog = fresh

    added = fuzzy = obsolete = unchanged = 0
    for message in catalog:
        if not message.id:
            continue                                          # the header
        if getattr(message, "obsolete", False):
            obsolete += 1
            continue
        if message.fuzzy:
            # A FUZZY LINE IS ONE THAT NEEDS A HUMAN: either a description was rewritten at the same
            # place (Babel moved the old translation onto the new msgid) or a translator flagged it.
            # Counted as fuzzy rather than new, so the summary says what actually has to be reviewed.
            fuzzy += 1
        elif message.id not in before:
            added += 1
        else:
            unchanged += 1
    if language == "en":
        # English IS the msgid: its catalog is the identity, so a reader inspecting it (or a tool
        # reading only msgstr) sees the text rather than a blank. The `fuzzy` mark Babel puts on a line
        # it carried over from a rewritten description is cleared here too: an English line is never a
        # translation awaiting review, and leaving it fuzzy would silently drop it from the compiled
        # .mo (the runtime would still answer correctly, via the msgid, but the catalog would lie).
        for message in catalog:
            if message.id and not getattr(message, "obsolete", False):
                message.string = message.id
                message.flags.discard("fuzzy")          # ``fuzzy`` is a PROPERTY derived from the flags
    return catalog, {"added": added, "fuzzy": fuzzy, "obsolete": obsolete, "unchanged": unchanged}


def render_po(catalog) -> str:
    """The catalog as it would be written — used by ``--check`` to compare without touching disk."""
    from babel.messages.pofile import write_po

    buffer = io.BytesIO()
    write_po(buffer, catalog, width=100, sort_output=True)
    return buffer.getvalue().decode("utf-8")


def _comparable(catalog) -> dict:
    """``{msgid: (msgstr, fuzzy, obsolete)}`` — the part of a catalog a run can change.

    The HEADER is deliberately excluded: it carries a fresh ``POT-Creation-Date`` on every run, so
    comparing the files byte-for-byte would report every catalog as out of date forever. What has to
    be in step is the ENTRIES — that is what a schema edit changes.
    """
    return {message.id: (message.string or "", bool(message.fuzzy),
                         bool(getattr(message, "obsolete", False)))
            for message in catalog if message.id}


def check(language: str, fresh) -> bool:
    """Is *language*'s catalog (and its compiled ``.mo``) in step with the schemas?"""
    from babel.messages.pofile import read_po

    path = LOCALE_DIR / language / "LC_MESSAGES" / f"{SCHEMA_DOMAIN}.po"
    if not path.is_file() or not path.with_suffix(".mo").is_file():
        return False
    try:
        with path.open("rb") as handle:
            on_disk = read_po(handle, locale=language, domain=SCHEMA_DOMAIN)
    except Exception:                                             # an unreadable catalog is stale
        return False
    return _comparable(on_disk) == _comparable(fresh)


def languages() -> list[str]:
    """Every language that has a ``locale/`` folder, base language first."""
    if not LOCALE_DIR.is_dir():
        return ["en"]
    found = sorted(child.name for child in LOCALE_DIR.iterdir()
                   if child.is_dir() and (child / "LC_MESSAGES").is_dir())
    return (["en"] if "en" in found else []) + [code for code in found if code != "en"]


def write_catalog(catalog, language: str) -> pathlib.Path:
    """Write ``schema.po`` AND compile ``schema.mo`` — the runtime reads the ``.mo``."""
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import write_po

    directory = LOCALE_DIR / language / "LC_MESSAGES"
    directory.mkdir(parents=True, exist_ok=True)
    po_path = directory / f"{SCHEMA_DOMAIN}.po"
    with po_path.open("wb") as handle:
        write_po(handle, catalog, width=100, sort_output=True)
    with po_path.with_suffix(".mo").open("wb") as handle:
        write_mo(handle, catalog, use_fuzzy=False)            # a fuzzy line is NOT served
    return po_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the schema-prose catalogs (locale/<lang>/LC_MESSAGES/schema.po + .mo).")
    parser.add_argument("--check", action="store_true",
                        help="report whether the catalogs are in step; write nothing (exit 1 if not)")
    parser.add_argument("--langs", default="",
                        help="comma-separated languages (default: every language in locale/)")
    args = parser.parse_args(argv)

    try:
        import babel                                                 # noqa: F401
    except ImportError:
        print("Babel is required for this tool: pip install babel", file=sys.stderr)
        return 2

    entries = collect()
    targets = [code.strip() for code in args.langs.split(",") if code.strip()] or languages()
    print(f"schemas scanned: {len(schema_files())}, descriptions found: {len(entries)}")
    stale = 0
    for language in targets:
        catalog, counts = merge(entries, language)
        path = LOCALE_DIR / language / "LC_MESSAGES" / f"{SCHEMA_DOMAIN}.po"
        if args.check:
            in_step = check(language, catalog)
            print(f"  {language}: {'OK' if in_step else 'OUT OF DATE'}"
                  f" (+{counts['added']} new, {counts['fuzzy']} fuzzy, {counts['obsolete']} obsolete)")
            stale += 0 if in_step else 1
        else:
            write_catalog(catalog, language)
            print(f"  {language}: written {path.relative_to(REPO_ROOT)} "
                  f"(+{counts['added']} new, {counts['fuzzy']} fuzzy, {counts['obsolete']} obsolete, "
                  f"{counts['unchanged']} kept)")
    if args.check and stale:
        print("\nRun `python3 -m services.webservice.schema_i18n` to update them.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":                                          # pragma: no cover - CLI
    raise SystemExit(main())
