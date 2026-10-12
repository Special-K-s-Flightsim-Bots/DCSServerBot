"""The schema-driven config PAGE GENERATOR — a pykwalify schema becomes a form.

WHAT THIS IS
------------
The admin console's config pages are not hand-written per file: a page is GENERATED from the very
schema the bot already validates that file against (``schemas/*.yaml``, ``plugins/<name>/schemas/
<name>_schema.yaml``). The schemas are pykwalify, and their rule tree is walkable, so the generator
walks it once and emits a tree of :class:`Field` records — the widget model a template renders and
the submit path reads back. A plugin that ships a schema therefore shows up in the console with no
frontend work.

THE MAPPING (schema construct -> control), stated exhaustively so nothing here is a guess:

====================  ===========================================================
schema construct       control
====================  ===========================================================
``type: bool``         switch (a checkbox + its companion hidden field)
``type: int|float``    number input (``range: {min,max}`` -> min/max attributes)
``type: str``          single-line text input
``type: text``         multi-line text area
``type: url``          single-line text input (a URL is a string to the schema)
``enum: [...]``        select (a scalar rule whose options are known)
``type: seq`` (scalar) row list of values, ``+ Add`` / ``✕`` per row
``type: seq`` (map)    row list of sub-forms, ``+ Add`` / ``✕`` per row
``type: map``          nested fieldset (``<details>`` beyond the first depth)
``regex;(a|b|…)``      TYPED SECTIONS — when the pattern is a FIXED list of literal
                       names the operator picks the one to add (a ``+`` offering
                       exactly those names); the chosen name renders exactly as if
                       the schema carried it literally. A pattern the parser cannot
                       reduce to a fixed list keeps the ``regex;(.+)`` fallback
``include: 'x'``       resolved (pykwalify partial schema, merged) AND made
                       VISIBLE: a cross-file partial renders as its own fieldset,
                       marked with the file that declares it (see below)
``regex;(.+)`` key     "Add entry" — a free-text key plus the rendered sub-form
``type: any`` + enum   layout chooser — "List" or "Single", both editors offered
``func: <name>``       the control is flagged **validate on save** (the generator
                       never guesses what the validator means); an ``any`` rule
                       with a ``func:`` and no structure renders as a text area
``required: true``     marked required — the operator must supply it
``nullable: false``    a blank is refused, not defaulted (no marker)
``default:``           prefilled; shown as the field's default
====================  ===========================================================

SPREAD OVER ``include``
-----------------------
A pykwalify ``include: 'x'`` names a partial declared ``schema;x:``. That partial is usually declared
in the SAME file (``element_schema`` in a plugin schema), but it is the WHOLE POINT of the construct
that it often is not: ~30 plugin schemas pull ``commands_schema`` / ``chat_commands_schema`` out of
``schemas/commands_schema.yaml``, a plugin's own sibling files carry its ballot schemas, and the node
schema pulls the whole ``extensions/**`` set through ``extensions_schema.yaml``. The bot's own loader
is what resolves those (``core/plugin.py`` adds ``schemas/commands_schema.yaml`` for a plugin;
``core/data/impl/nodeimpl.py`` adds ``extensions/**/*_schema.yaml`` for a node), so
:func:`load_schema` reads the SAME set of files (see :func:`schema_files_for`) and merges their
partials into ONE lookup — a cross-file include then resolves exactly as the validator resolves it.

BECAUSE AN INCLUDE IS A **SCHEMA** PARTIAL, NOT A VALUE REFERENCE, the generator also makes the
resolution VISIBLE, so the operator is never left guessing where an edit lands:

* a partial declared in ANOTHER file renders as its own fieldset, carrying the declaring file's name
  (``Field.include_source``), and the page states the save rule beside it: the fields DESCRIBE the
  block, the VALUES live in the file being edited — an edit is written into the INCLUDING config
  file, never into the schema that describes it;
* an include that would re-enter a partial already on the stack is a CYCLE: the walk STOPS there (the
  cycle guard, carried through the recursion — see :func:`_resolve_chain`) and marks the field
  (``Field.include_cycle``) with a clear sentence rather than recursing forever or crashing.

THE SCHEMA CARRIES NO PROSE, AND THAT IS ON PURPOSE
---------------------------------------------------
Every field's label is its **schema key**, printed verbatim. The architect's rule is that the
schemas hold identifiers, not sentences, so a key stays English in every language: translating a key
would break the link between the page and the file it writes. What the generator DOES compose are the
control's own chrome words (:data:`KIND_LABELS`, the default/required markers) — those are born
wrapped (``i18n._``) and filled in every catalog. Do not "fix" the English keys: they are not
untranslated, they are untranslatable by design.

NO WRITE PATH HERE
------------------
This module only DESCRIBES a schema and reads/writes an in-memory document. It never opens a file to
write one: a console save goes through the ONE primitive (``core.utils.configwrite``). ``apply_form``
mutates the round-trip document the primitive hands it, so comments and formatting survive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import i18n

__all__ = [
    "KIND_BOOL", "KIND_NUMBER", "KIND_TEXT", "KIND_MULTILINE", "KIND_SELECT",
    "KIND_LIST", "KIND_SEQ", "KIND_MAP", "KIND_REGEX", "KIND_FUNC", "KIND_CHOICE",
    "KIND_LABELS", "VALIDATE_ON_SAVE_LABEL", "DEFAULT_LABEL", "REQUIRED_LABEL",
    "UnknownFieldError", "DuplicateEntryError", "encode_token", "decode_token",
    "Field", "Schema", "load_schema", "schema_files_for", "resolve", "describe", "leaves",
    "value_at", "collect_current", "apply_form", "read_document", "find_field",
    "flatten", "element_leaves", "form_leaves", "rows_for", "custom_entries", "regex_value",
    "relative_name", "scopes", "alternatives", "SECTION_LABEL", "section_state", "view_id",
    "BLOCK_KINDS", "is_mandatory", "field_set", "field_shown", "hidden_options", "visibility",
    "encode_path", "decode_path", "REMOVED_COMPANION", "REMOVE_ACK_COMPANION",
]

# ------------------------------------------------------------------ the control kinds

#: a checkbox + its companion hidden field
KIND_BOOL = "bool"
#: ``<input type="number">`` with optional min/max
KIND_NUMBER = "number"
#: ``<input type="text">``
KIND_TEXT = "text"
#: ``<textarea>``
KIND_MULTILINE = "multiline"
#: ``<select>`` of the schema's ``enum``
KIND_SELECT = "select"
#: a ``seq`` of scalars — a row list of values
KIND_LIST = "list"
#: a ``seq`` of maps — a row list of sub-forms
KIND_SEQ = "seq"
#: a ``map`` — a fieldset
KIND_MAP = "map"
#: an unbounded-key map (``regex;(.+)``) — free-text keys with a sub-form each
KIND_REGEX = "regex"
#: a rule the generator cannot give a faithful widget (``func:`` on an ``any``, an exotic type) —
#: rendered as an editable value flagged "validate on save"
KIND_FUNC = "func"
#: ``type: any`` with an ``enum`` of includes — a chooser between two declared layouts
KIND_CHOICE = "choice"

#: The chrome word a control of each kind shows, beside its key. These are the strings the generator
#: composes, so they are born wrapped (``i18n._``) and filled in every catalog.
KIND_LABELS: dict[str, str] = {
    KIND_BOOL: i18n._("Switch"),
    KIND_NUMBER: i18n._("Number"),
    KIND_TEXT: i18n._("Text"),
    KIND_MULTILINE: i18n._("Text (multi-line)"),
    KIND_SELECT: i18n._("Select"),
    KIND_LIST: i18n._("List of values"),
    KIND_SEQ: i18n._("List of entries"),
    KIND_MAP: i18n._("Group"),
    KIND_REGEX: i18n._("Custom entries"),
    KIND_FUNC: i18n._("Value validated on save"),
    KIND_CHOICE: i18n._("List or single"),
}

#: THE CHROME WORD a ``regex;`` container shows when its pattern is a FIXED list of literal names
#: (``regex;(frontend|client)``): the block is not free-text "custom entries" any more but a named
#: SECTION the operator picks from the schema's own list. i18n-marked like every other kind word.
SECTION_LABEL = i18n._("Sections")

#: The badge a control carrying a ``func:`` shows. Never a guess about the validator's meaning.
VALIDATE_ON_SAVE_LABEL = i18n._("validate on save")
#: The marker beside a field whose value is the schema's ``default:``.
DEFAULT_LABEL = i18n._("default")
#: The marker beside a field the schema makes required (``required: true``) — the operator MUST
#: supply it. A field carrying only ``nullable: false`` is NOT required and carries NO label at all:
#: ``nullable: false`` still REFUSES an empty value (the schema enforces it), but that rule is not
#: worth a badge — the marker was removed as noise, so the two facts are never
#: conflated and no field is mislabelled "required" for it.
REQUIRED_LABEL = i18n._("required")

#: pykwalify ``func:`` names that select a *layout* rather than validate a scalar value. A rule
#: carrying one of these is NOT flagged "validate on save": the func says how the value is shaped
#: (a list, a map, either), which the structural widget already expresses.
_LAYOUT_FUNCS = frozenset({
    "seq_or_map", "bool_or_map", "str_or_map", "int_or_map",
    "bool_or_list", "str_or_list", "int_or_list", "text_or_list",
    "int_csv_or_list", "str_csv_or_list", "any_of",
    # node/element/main-structure funcs are WHOLE-DOCUMENT structure validators; the map they sit on
    # still renders from its ``mapping``, and re-flagging every child as "validate on save" would be
    # noise. The save's schema validation is what enforces them.
    "is_node", "is_element", "is_server", "check_main_structure", "deprecated", "obsolete",
})

_SCALAR_TYPES = {"str": KIND_TEXT, "text": KIND_MULTILINE, "url": KIND_TEXT}
_NUMBER_TYPES = {"int", "float"}


# ------------------------------------------------------------------ the schema model

@dataclass(frozen=True)
class Schema:
    """A parsed pykwalify schema: its root rule, its ``schema;<name>`` partials, and where they live.

    ``partials`` is the merged lookup the walk resolves ``include:`` against — the primary file's own
    partials PLUS those of the sibling files the bot's own loader would read (see
    :func:`schema_files_for`), so a cross-file include resolves exactly as the validator resolves it.
    ``included_files`` maps a partial name to the BASENAME of the OTHER file that declared it (a
    partial declared in the primary file is absent) — the "belonging to the included file" marker the
    page shows. ``primary`` is the primary file's own basename.
    """

    root: dict
    partials: dict
    included_files: dict = field(default_factory=dict)
    primary: str = ""


@dataclass(frozen=True)
class Field:
    """One control the page renders, and the path in the document it reads/writes.

    ``path`` is the tuple of keys from the document root; a ``*`` token marks "one element of a
    sequence (by index) or one regex entry (by free-text key)". ``name`` is that path joined with
    ``.`` — the key a template uses to look its current value up and to name the form field (the
    submit path prefixes it with ``f.``).
    """

    key: str
    kind: str
    path: tuple = ()
    required: bool = False
    nullable: bool = True
    default: Any = None
    enum: tuple = ()
    minimum: Any = None
    maximum: Any = None
    func: str | None = None
    validate_on_save: bool = False
    children: tuple = ()
    element: "Field | None" = None
    regex: str | None = None
    #: the FIXED, literal names a ``regex;`` pattern offers (``regex;(frontend|client)`` ->
    #: ``("frontend", "client")``) — the schema's own alternative list, READ from the pattern and
    #: never hardcoded. Empty (``()``) when the pattern is not a finite list of literals (an
    #: unbounded ``regex;(.+)``, a ``\d+``, a time range), which keeps the honest "Add entry" fallback.
    alternatives: tuple = ()
    #: the pykwalify partial name the rule ``include:``d (``None`` when the rule was not an include)
    include: str | None = None
    #: the BASENAME of the OTHER file that declared the included partial — the page marks a fieldset
    #: with it. ``None`` when the partial was declared in the primary file (nothing to mark).
    include_source: str | None = None
    #: the partial name an ``include:`` re-entered (a CYCLE the guard stopped at), or ``None``
    include_cycle: str | None = None
    #: the i18n-wrapped chrome word for this control's kind (never empty)
    kind_label: str = ""

    @property
    def name(self) -> str:
        return ".".join(self.path)

    @property
    def has_default(self) -> bool:
        return self.default is not None


# ------------------------------------------------------------------ loading and resolving

def _read_partials(path: Path) -> tuple[dict, dict]:
    """Read ONE schema file: its ``schema;<name>`` partials and its top-level (root) keys."""
    data = YAML(typ="rt").load(path.read_text(encoding="utf-8")) or {}
    partials: dict[str, dict] = {}
    root: dict = {}
    for key, value in data.items():
        if isinstance(key, str) and key.startswith("schema;"):
            partials[key.split(";", 1)[1]] = value
        else:
            root[key] = value
    return partials, root


def schema_files_for(path: str | Path) -> list[str]:
    """The schema files the bot's OWN loader reads for *path* — the exact set ``include:`` resolves in.

    pykwalify MERGES every file's top-level keys into one dict and refuses a key seen twice, so this
    list is not "every related file" — it is exactly what ``core/utils/validators.py`` is handed, and
    the bot keeps those sets collision-free on purpose. Stated as the three cases the loader has:

    * **a plugin schema** (``plugins/<name>/schemas/<file>``) — every ``*.yaml`` beside it (a plugin's
      ballots live in sibling files, and their ``schema;`` names are disjoint) PLUS
      ``schemas/commands_schema.yaml``, which ``core/plugin.py`` appends for every plugin;
    * **the node schema** (``schemas/nodes_schema.yaml``) — itself PLUS every
      ``extensions/**/*_schema.yaml``, exactly as ``core/data/impl/nodeimpl.py`` extends it. Those
      extension files declare only ``schema;`` partials, so nothing collides;
    * **any other core schema** (``schemas/<file>``, an extension's own schema) — itself alone, which is
      what the bot validates a ``main.yaml``/``servers.yaml`` against. Sibling CORE schemas are NOT
      added: each carries its own root keys, and merging two of them is a pykwalify ``CoreError``.

    The primary file is always first and always present, so a caller can hand the list straight to
    ``core.utils.validators.validate`` as ``schemas``.
    """
    path = Path(path).resolve()
    parent = path.parent
    files: list[str] = [str(path)]
    seen: set[str] = {str(path)}

    def _add(candidate: Path) -> None:
        if candidate.is_file() and str(candidate) not in seen:
            seen.add(str(candidate))
            files.append(str(candidate))

    def _add_siblings() -> None:
        for sibling in sorted(parent.glob("*.yaml")):
            _add(sibling)

    if parent.name == "schemas" and parent.parent.parent.name == "plugins":
        # a PLUGIN schema (``plugins/<name>/schemas/<file>``): core/plugin.py appends
        # schemas/commands_schema.yaml — the repo root is four directories up from the schema file
        _add_siblings()
        _add(parent.parent.parent.parent / "schemas" / "commands_schema.yaml")
    elif parent.name == "schemas":
        # a CORE schema (``schemas/<file>``): the node schema pulls the whole extensions tree; any
        # other core schema is validated ALONE (its siblings carry their own root keys).
        if path.name == "nodes_schema.yaml":
            for extension in sorted((parent.parent / "extensions").rglob("*_schema.yaml")):
                _add(extension)
    else:
        # an extension's own schema (``extensions/<name>/schemas/<file>``): its disjoint siblings
        _add_siblings()
    return files


def load_schema(path: str | Path, *, schema_files: "list[str] | None" = None) -> Schema:
    """Parse a pykwalify schema *file* into its root rule and its merged ``schema;<name>`` partials.

    ``schema_files`` is the set of files whose partials are ALSO collected (the cross-file half of
    ``include:``). When it is ``None`` the conventional set is computed from *path* by
    :func:`schema_files_for`, so a caller that knows nothing about the bot's loader still gets
    cross-file includes resolved; pass an explicit list to control it (the tests do). The primary file's
    own partials WIN any name collision and are not listed in ``included_files`` — that map names only
    the parts that come from a DIFFERENT file, which is what the page marks.
    """
    path = Path(path).resolve()
    names = [str(path), *(schema_files or schema_files_for(path))]
    partials: dict[str, dict] = {}
    included_files: dict[str, str] = {}
    root: dict = {}
    primary = path.name
    for index, name in enumerate(names):
        candidate = Path(name)
        if not candidate.is_file():
            continue
        file_partials, file_root = _read_partials(candidate)
        if index == 0:
            root = file_root
        for partial_name, value in file_partials.items():
            if partial_name in partials:
                continue                       # the primary file's own partial (or a first sibling) wins
            partials[partial_name] = value
            if candidate.name != primary:
                included_files[partial_name] = candidate.name
    return Schema(root=root, partials=partials, included_files=included_files, primary=primary)


def _resolve_chain(rule: Any, partials: dict, seen: tuple) -> tuple[dict, tuple, str | None]:
    """Resolve ``include:`` down the chain; return the rule, the includes walked, AND any cycle closed.

    The walked names are returned, not just used as a local guard, because the generator carries them
    through its OWN recursion (``_field`` -> ``_children_from_mapping`` -> ``_field``). A
    self-referential schema — ``menus_schema.yaml``'s element is a list whose element is that element
    again, and ``presets_schema.yaml``'s ``modify_schema`` includes a sequence of itself — can only
    terminate if every nesting level re-enters with the SAME set already on the stack. A FRESH
    ``_seen`` per level (the old bug) walks such a cycle forever.

    The third value is the CYCLE: the name of the include that was already on the stack, so the caller
    can say so on the page (the guard STOPS the walk — it never recurses forever, and it never crashes).
    """
    if not isinstance(rule, dict):
        return {}, seen, None
    name = rule.get("include")
    if not name:
        return dict(rule), seen, None
    if name in seen:
        return dict(rule), seen, name
    base = dict(partials.get(name, {}))
    rest = {key: value for key, value in rule.items() if key != "include"}
    base.update(rest)
    return _resolve_chain(base, partials, seen + (name,))


def resolve(rule: Any, partials: dict, *, _seen: tuple = ()) -> dict:
    """Follow ``include: 'x'`` through *partials* and return the fully-resolved rule dict.

    A partial is merged UNDER the including rule's own keys, so ``{include: x, nullable: false}``
    keeps the override. A cycle is broken by leaving the ``include`` key in place rather than
    recursing forever (a schema should not have one; a bug in one must not hang the console).
    """
    return _resolve_chain(rule, partials, _seen)[0]


# ------------------------------------------------------------------ the walk

def _scalar_kind(type_: str | None, enum: Any, func: str | None) -> str:
    """The kind a leaf rule maps to — select when an ``enum`` names literal options, else by type."""
    if isinstance(enum, list) and enum and func not in _LAYOUT_FUNCS:
        return KIND_SELECT
    if type_ in _SCALAR_TYPES:
        return _SCALAR_TYPES[type_]
    if type_ in _NUMBER_TYPES:
        return KIND_NUMBER
    if type_ == "bool":
        return KIND_BOOL
    return KIND_FUNC


def _element_rule(rule: dict) -> dict:
    """The element rule of a ``seq``: pykwalify allows one ``sequence`` entry."""
    sequence = rule.get("sequence")
    if isinstance(sequence, list) and sequence:
        return sequence[0]
    return {}


#: The literal characters an alternative NAME may be built from, unescaped (letters, digits, ``_`` and
#: ``-``). Anything else — an unescaped regex metacharacter (``. * + ? [ ] { } ( ) | ^ $ \``) — makes
#: the alternative a PATTERN rather than a literal, so the whole key drops to the honest fallback.
_ALTERNATIVE_LITERAL = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _strip_one_paren_pair(pattern: str) -> str:
    """*pattern* with ONE outer ``(...)`` pair removed, when that pair wraps the WHOLE pattern.

    ``(frontend|client)`` -> ``frontend|client``; ``(a|b)c`` is NOT wrapped (the close paren is not
    the last character) and is returned unchanged, so a pattern with trailing regex is not mistaken
    for a pure alternation. Escapes are honoured while scanning, so a ``\\)`` inside does not end the
    pair early.
    """
    if not (pattern.startswith("(") and pattern.endswith(")")):
        return pattern
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pattern[1:-1] if index == len(pattern) - 1 else pattern
        index += 1
    return pattern


def _split_top_level(pattern: str) -> list[str] | None:
    """Split *pattern* on ``|`` at paren depth 0, honouring escapes; ``None`` when unbalanced."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            current.append(char)
            if index + 1 < len(pattern):
                current.append(pattern[index + 1])
                index += 1
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if depth != 0:
        return None
    parts.append("".join(current))
    return parts


def _literal_name(part: str) -> str | None:
    r"""The literal name *part* spells, or ``None`` when it is a pattern, not a fixed literal.

    A character-class escape (``\d``, ``\w``, ``\s`` … — any escaped LETTER or digit) is a pattern;
    an escaped punctuation character (``\*``, ``\(``, ``\_``, ``\-`` …) is that literal character.
    Every unescaped character must be in :data:`_ALTERNATIVE_LITERAL`, so an unescaped metacharacter
    (``.+``, ``\d+``, a ``$``/``^`` anchor) makes the whole alternative fail.
    """
    if not part:
        return None
    out: list[str] = []
    index = 0
    while index < len(part):
        char = part[index]
        if char == "\\":
            index += 1
            if index >= len(part):
                return None
            escaped = part[index]
            if escaped.isalnum():
                return None                    # \d \w \s \b \1 … are classes/backrefs, not literals
            out.append(escaped)
            index += 1
            continue
        if char not in _ALTERNATIVE_LITERAL:
            return None                        # an unescaped metacharacter -> a pattern, not a name
        out.append(char)
        index += 1
    name = "".join(out)
    return name or None


def alternatives(pattern: str) -> tuple[str, ...]:
    """The FIXED, literal names a pykwalify ``regex;`` *pattern* offers — or ``()`` when it offers none.

    THE SCHEMA IS THE SOURCE OF TRUTH. A pattern that is a pure alternation of literal names — with or
    without one wrapping ``(...)`` pair, e.g. ``(frontend|client)``, ``(backend|server)``,
    ``(on_add|on_remove|on_leave)``, ``(Airbase|Carrier|FARP|\\*)`` (the ``\\*`` being a literal ``*``)
    — yields its names in the schema's own order, so the page can offer exactly those and render each
    exactly as a literal-named section would. A pattern that is NOT a finite list of literals — the
    unbounded ``(.+)``/``(.*)``, a ``(\\d+)``, the port-range ``(…)-(…)``, the ``_OK_|OK|(OK)|…``
    status alternation is a finite list (accepted) but a ``\\d``/``+`` inside one alternative is not —
    yields ``()`` and keeps the honest "Add entry" fallback. Nothing is hardcoded per plugin.
    """
    if not pattern:
        return ()
    body = _strip_one_paren_pair(pattern)
    parts = _split_top_level(body)
    if parts is None:
        return ()
    names: list[str] = []
    for part in parts:
        name = _literal_name(part)
        if name is None:
            return ()
        names.append(name)
    return tuple(names) if names else ()


def _children_from_mapping(mapping: dict, partials: dict, included_files: dict, path: tuple,
                           _seen: tuple = ()) -> tuple:
    """The child fields of a ``map`` rule — regex keys become a :data:`KIND_REGEX` "Add entry".

    A pykwalify map key may be ``regex;<pattern>`` (an unbounded key), which is not a field NAME but
    a shape for keys the operator invents: it renders as one "custom entries" control whose element
    is the sub-form the pattern's rule describes. ``_seen`` carries the include chain already walked,
    so a cyclic schema terminates (see :func:`_resolve_chain`).

    WHEN the pattern is a FIXED list of literal names (:func:`alternatives`), the container carries
    those names on ``alternatives`` and its kind word becomes :data:`SECTION_LABEL` — the page then
    offers exactly those names as sections, each rendered like a literal-named block, instead of a
    free-text key. The element (the sub-form) is the same in both cases.
    """
    children = []
    for child_key, child_rule in mapping.items():
        if isinstance(child_key, str) and child_key.startswith("regex;"):
            pattern = child_key.split(";", 1)[1]
            names = alternatives(pattern)
            sub = _field("regex", child_rule, partials, included_files, path + ("*",),
                         regex=pattern, _seen=_seen)
            children.append(Field(key=child_key, kind=KIND_REGEX, path=path,
                                  element=sub, regex=pattern, alternatives=names,
                                  kind_label=SECTION_LABEL if names else KIND_LABELS[KIND_REGEX]))
        else:
            children.append(_field(child_key, child_rule, partials, included_files,
                                   path + (child_key,), _seen=_seen))
    return tuple(children)


def _field(key: str, rule: Any, partials: dict, included_files: dict, path: tuple, *,
           regex: str | None = None, _seen: tuple = ()) -> Field:
    """One control, built from one (resolved) rule.

    ``_seen`` is the include chain already on the stack; it is threaded into every nested walk so a
    self-referential schema terminates rather than recursing forever. ``included_files`` names the
    OTHER file each cross-file partial lives in, so a fieldset can say which file a block belongs to;
    the include NAME the rule wrote and any cycle the guard closed are recorded on the field.
    """
    include = rule.get("include") if isinstance(rule, dict) else None
    rule, seen, cycle = _resolve_chain(rule, partials, _seen)
    type_ = rule.get("type")
    func = rule.get("func")
    enum = rule.get("enum")
    rng = rule.get("range") or {}
    required = bool(rule.get("required"))
    nullable = rule.get("nullable", True)
    validate = bool(func) and func not in _LAYOUT_FUNCS

    common = dict(
        key=key, path=path, required=required, nullable=nullable,
        default=rule.get("default"), func=func,
        minimum=rng.get("min"), maximum=rng.get("max"), regex=regex,
        validate_on_save=validate,
        include=include,
        include_source=included_files.get(include) if include else None,
        include_cycle=cycle,
    )

    if type_ == "map":
        mapping = rule.get("mapping") or {}
        if mapping:
            return Field(kind=KIND_MAP,
                         children=_children_from_mapping(mapping, partials, included_files, path, seen),
                         kind_label=KIND_LABELS[KIND_MAP], **common)
        # a map with no ``mapping`` (a ``func`` that shapes it, or an empty map): the generator cannot
        # invent its keys — it is an editable value flagged "validate on save", never a guess.
        common["validate_on_save"] = True
        return Field(kind=KIND_FUNC, kind_label=KIND_LABELS[KIND_FUNC], **common)

    if type_ == "seq":
        element = _field(key, _element_rule(rule), partials, included_files, path + ("*",),
                         _seen=seen)
        scalar_element = element.kind in (KIND_BOOL, KIND_NUMBER, KIND_TEXT, KIND_MULTILINE,
                                          KIND_SELECT, KIND_FUNC)
        kind = KIND_LIST if scalar_element else KIND_SEQ
        return Field(kind=kind, element=element, kind_label=KIND_LABELS[kind], **common)

    # ``type: any`` (or none) with an ``enum`` of include NAMES and a layout ``func`` is the schema's
    # "either of these two shapes" — offer BOTH editors (the mockup's List/Single chooser).
    if isinstance(enum, list) and enum and all(isinstance(n, str) for n in enum) \
            and (func in _LAYOUT_FUNCS or type_ in (None, "any")):
        choices = tuple(_field(name, {"include": name}, partials, included_files, path, _seen=seen)
                        for name in enum)
        if any(choice.kind in (KIND_MAP, KIND_SEQ, KIND_LIST) for choice in choices):
            return Field(kind=KIND_CHOICE, children=choices, enum=tuple(enum),
                         kind_label=KIND_LABELS[KIND_CHOICE], **common)

    if type_ in (None, "any"):
        # a rule the generator cannot give a faithful widget — an unresolved ``include`` (a partial the
        # loader could not find), a bare ``any``, a ``func``-shaped value. It is an editable value the
        # SCHEMA must vouch for on the write, so it carries the "validate on save" badge — the same
        # promise the mapping table makes, and it does NOT depend on the rule carrying a ``func``.
        common["validate_on_save"] = True
        return Field(kind=KIND_FUNC, kind_label=KIND_LABELS[KIND_FUNC], **common)

    kind = _scalar_kind(type_, enum, func)
    return Field(kind=kind, enum=tuple(enum) if isinstance(enum, list) and kind == KIND_SELECT else (),
                 kind_label=KIND_LABELS[kind], **common)


def describe(schema: Schema) -> Field:
    """The whole page as one :class:`Field` tree — the root a ``map`` whose children are the keys.

    A schema whose root is not a map (a bare ``seq``, say) yields a single list control. That is not
    a config file's shape today, but the generator states the answer rather than raising.
    """
    root, seen, _cycle = _resolve_chain(schema.root, schema.partials, ())
    if root.get("type") == "map" and root.get("mapping"):
        return Field(key="", kind=KIND_MAP, path=(),
                     children=_children_from_mapping(root["mapping"], schema.partials,
                                                     schema.included_files, (), seen),
                     kind_label=KIND_LABELS[KIND_MAP])
    return _field("", root, schema.partials, schema.included_files, (), _seen=seen)


# ------------------------------------------------------------------ reading the tree

def flatten(tree: Field) -> list[Field]:
    """Every field in the tree, in document order (each ``element``/``children`` included)."""
    out: list[Field] = []

    def walk(field: Field) -> None:
        out.append(field)
        for child in field.children:
            walk(child)
        if field.element is not None:
            walk(field.element)

    walk(tree)
    return out


def leaves(tree: Field) -> list[Field]:
    """The editable leaves: everything but the container kinds."""
    return [field for field in flatten(tree)
            if field.kind not in (KIND_MAP, KIND_SEQ, KIND_REGEX)]


def scopes(tree: Field) -> list[dict]:
    """The page's SCOPES (its tabs): the DEFAULT block, plus one per override the schema distinguishes.

    A plugin/service schema distinguishes them: a top-level ``DEFAULT`` key (the shared block) and,
    beside it, one override per element/node (a ``regex;``/``is_element``/``is_node`` map or a
    ``check_main_structure`` map). A FLAT schema (``main_schema.yaml``) distinguishes nothing, so its
    whole mapping is the one DEFAULT scope. Each scope is ``{label, fields}`` and ``fields`` are the
    :class:`Field`s the page renders under it — so a later card that makes the tabs switchable needs
    only presentation, not a second model. Order is DEFAULT first (the active tab), the overrides after.
    """
    default_child = next((child for child in tree.children if child.key == "DEFAULT"), None)
    if default_child is None:
        return [{"label": "DEFAULT", "fields": list(tree.children)}]
    overrides = [child for child in tree.children
                 if child.key != "DEFAULT" and child.kind in (KIND_REGEX, KIND_MAP)]
    out = [{"label": "DEFAULT", "fields": list(default_child.children)}]
    out += [{"label": child.key, "fields": [child]} for child in overrides]
    return out


def _subform_leaf(base: tuple, leaf: Field) -> bool:
    """Whether *leaf*, inside a sub-form rooted at *base*, can be named by a POSTED field.

    A CONTAINER kind (``map``/``seq``/``regex``/``choice``) is not a control of its own, and a leaf
    whose relative name still carries a ``*`` — an element of a NESTED sequence, ``files.*.source`` —
    has no fixed name to post at all. Rendering either would emit a field the save cannot place, which
    is REFUSED rather than dropped, so a save of the WHOLE page would never succeed. Leaving them out
    is what lets a ``seq``/``regex`` whose ELEMENT is a layout chooser — ``schemas/presets_schema.yaml``
    — render its MAP option faithfully: the chooser and its list option collapse away, and the maps'
    own fields remain.
    """
    if leaf.kind in (KIND_MAP, KIND_LIST, KIND_SEQ, KIND_REGEX, KIND_CHOICE):
        return False
    return "*" not in leaf.path[len(base):]


def element_leaves(field: Field) -> list[Field]:
    """The leaf sub-fields of a sequence/regex element (for rendering one row)."""
    if field.element is None:
        return []
    base = field.element.path
    return [leaf for leaf in flatten(field.element) if _subform_leaf(base, leaf)]


def form_leaves(field: Field) -> list[tuple]:
    """``[(leaf, relative name)]`` for a field whose SUB-form the page renders inline.

    Used for a sequence/regex element and for each option of a layout chooser: the base path is the
    element's when there is one (a ``seq``/``regex``), else the field's own (a ``map`` option), so a
    rendered sub-control's name is what a row dict is keyed by. Only leaves a POSTED field can name
    are returned — see :func:`_subform_leaf`.
    """
    base = field.element.path if field.element is not None else field.path
    return [(leaf, ".".join(leaf.path[len(base):]))
            for leaf in flatten(field)
            if _subform_leaf(base, leaf)]


def relative_name(leaf: Field, field: Field) -> str:
    """The dotted name of *leaf* relative to *field*'s element (which one row's dict is keyed by)."""
    element_path = field.element.path if field.element is not None else field.path
    rel = leaf.path[len(element_path):]
    return ".".join(rel)


#: The separator :func:`view_id` joins with — a control character no schema key or operator name can
#: carry, so a path and a key can never be confused for one another.
_VIEW_ID_SEP = "\x1f"


def view_id(field: Field) -> str:
    """A key that names *field* UNIQUELY among the fields of one rendered scope.

    A ``regex;`` container's ``path`` is its PARENT map's path (its ``name`` is the parent's name), so
    two regex containers in the SAME mapping render with the SAME ``name`` — a container view map
    keyed by ``name`` alone could hold only ONE of them, which is exactly why Olympus's
    ``(frontend|client)`` and ``(backend|server)`` sections both read the same entries. Joining the
    name with the field's own schema KEY disambiguates siblings (a mapping's keys are unique), and
    the path still keeps two equally-keyed containers at different depths apart. For every ordinary
    field this is just its name for a caller that does not care — the map key, never a form field.
    """
    return f"{field.name}{_VIEW_ID_SEP}{field.key}"


def _row_value(row: Any, rel: str) -> Any:
    node = row
    for token in ([t for t in rel.split(".") if t] if rel else []):
        if isinstance(node, dict):
            node = node.get(token)
        else:
            return None
    return node


def rows_for(field: Field, value: Any) -> list[dict]:
    """The rows a sequence/regex field renders: ``[{index, values: {relative name: value}}]``."""
    items = value if isinstance(value, list) else []
    out = []
    for index, row in enumerate(items):
        out.append({"index": index,
                    "values": {relative_name(leaf, field): _row_value(row, relative_name(leaf, field))
                               for leaf in element_leaves(field)}})
    return out


def custom_entries(field: Field, value: Any) -> list[dict]:
    """The free-text entries a regex field renders: ``[{key, enc_key, values: {relative name: value}}]``.

    ``key`` is the operator's own key, printed verbatim (its input's value). ``enc_key`` is the SAME key
    with its path-token escapes applied (:func:`encode_token`) — the form a field NAME must use, so a
    key containing ``.``/``%`` does not split into two tokens on save. The template names every control
    of this entry from ``enc_key`` while showing ``key``.
    """
    if not isinstance(value, dict):
        return []
    out = []
    for key, row in value.items():
        out.append({"key": key, "enc_key": encode_token(key),
                    "values": {relative_name(leaf, field): _row_value(row, relative_name(leaf, field))
                               for leaf in element_leaves(field)}})
    return out


def _literal_sibling_keys(tree: Field, field: Field) -> frozenset:
    """The literal keys the mapping a regex container *field* sits in declares ALONGSIDE it.

    A regex container's ``path`` is its PARENT mapping's path (the operator's keys are siblings of the
    mapping's literal keys), so its literal siblings are the fields exactly one path-token deeper whose
    leading tokens are the regex's own path. The regex ELEMENT's own ``*`` token (the sub-form a new
    entry is cloned from) is excluded, so what remains is the set of document keys the mapping already
    covers by NAME — which a "custom entries" listing must never treat as an operator's invention.
    """
    parent = field.path
    literal = set()
    for other in flatten(tree):
        tokens = other.path
        if len(tokens) == len(parent) + 1 and tokens[:len(parent)] == parent:
            token = tokens[-1]
            if token != "*":
                literal.add(token)
    return frozenset(literal)


def regex_value(tree: Field, field: Field, document: Any) -> Any:
    """The mapping a regex field's free-text entries are read from — or ``None``.

    The value is the one at the regex's PARENT path (:func:`value_at`), read from the DOCUMENT rather
    than from ``collect_current``'s leaf values (that map fills leaves only, so a container's own value
    is never in it — which is why a regex page rendered no entry for a file that was full of them).

    Every key the mapping's literal children already cover is REMOVED (see
    :func:`_literal_sibling_keys`), so the listing is EXACTLY the keys the operator invented: never a
    fixed key that sits beside the regex, and never ``DEFAULT`` — a top-level ``regex;(.+)`` would
    otherwise report its parent path, the whole document, and list every top-level key as an entry.
    """
    parent = value_at(document, field.path)
    if not isinstance(parent, dict):
        return None
    literal = _literal_sibling_keys(tree, field)
    result = {key: value for key, value in parent.items() if key not in literal}
    if field.alternatives:
        # a FIXED-ALTERNATIVE container owns ONLY its own allowed names: a sibling regex container's
        # keys (backend/server beside frontend/client) are not literal children, so without this the
        # frontend container would list backend too. Filtering to the schema's own list keeps each
        # container's view exactly the names IT offers.
        allowed = set(field.alternatives)
        result = {key: value for key, value in result.items() if key in allowed}
    return result


def section_state(field: Field, entries) -> dict:
    """For a FIXED-ALTERNATIVE regex container: which allowed names are PRESENT, and which are MISSING.

    ``entries`` is the container's ``custom_entries`` list (``[{key, enc_key, values}]``) — the names
    the document already carries, in document order. ``present`` is their keys; ``missing`` is the
    schema's own alternatives that are NOT present, in the schema's own order — the exact list the
    "+ Add section" control offers. A non-alternative field yields an empty mapping, so the template's
    fallback branch is used.
    """
    present = [entry["key"] for entry in entries]
    missing = [name for name in field.alternatives if name not in present]
    return {"present": present, "missing": missing}


# -------------------------------------------- show / hide: mandatory + set, the rest behind a picker
#
# THE EDITOR SHOWS ONLY WHAT MATTERS. A schema with a lot of OPTIONALITY used to render every field,
# set or not, so a big schema produced a wall of controls and the few values that actually matter were
# lost in it. The rule: show a field when it is MANDATORY or the file ALREADY CARRIES it;
# hide every OPTIONAL, ABSENT field behind a "+ Add" picker, and let the operator add it (and remove it
# again). Two words carry the whole rule:
#
# * MANDATORY means the schema says ``required: true`` (:func:`is_mandatory`). ``nullable: false`` is
#   NOT mandatory — it refuses an empty VALUE, not an absent KEY — so it never makes a field
#   always-shown (the same distinction :data:`REQUIRED_LABEL` already draws).
# * SET means the file already carries the key (:func:`field_set`). "Optional" means "may be absent",
#   never "may be hidden": ANYTHING SET IS ALWAYS SHOWN, whatever the schema says, so a value in the
#   file can never be hidden from the operator and no save can silently drop it.
#
# The picker offers the OPTIONAL, ABSENT children of a container, FIELDS before BLOCKS, each carrying
# a field's own kind word as its one-line description (the schemas carry IDENTIFIERS, not prose — see
# the module docstring — so the schema's description of a control IS its kind). Adding one renders the
# control the schema would have rendered, EMPTY, at the field's own document path.

#: The kinds that are a BLOCK (a group of controls) rather than a single field. The picker groups by
#: this: a FIELDS group first, then a BLOCKS group, so "add a setting" and "add a whole block" read as
#: two genuinely different choices.
BLOCK_KINDS = frozenset({KIND_MAP, KIND_SEQ, KIND_REGEX, KIND_CHOICE, KIND_LIST})


def is_mandatory(field: Field) -> bool:
    """MANDATORY: the schema marks the field ``required: true`` — the ONE always-shown signal.

    ``nullable: false`` is NOT mandatory (it refuses an empty value, not an absent key), so it never
    makes a field always-shown. See :func:`field_shown` for the one visibility rule.
    """
    return bool(field.required)


def field_set(tree: Field, field: Field, document: Any) -> bool:
    """Whether *document* ALREADY CARRIES *field* — the "anything set is always shown" test.

    A leaf, a ``seq``, a ``map`` (a block) or a chooser is SET when the document carries its key AT ALL
    — an EMPTY block or list is still set, it exists and must stay visible. A ``regex;`` container has
    no key of its own (its entries are its PARENT mapping's keys), so it is SET when at least one of
    its own entries is present (:func:`regex_value` already filters out the mapping's literal siblings).
    """
    if field.kind == KIND_REGEX:
        return bool(regex_value(tree, field, document))
    return not _path_missing(document, field.path)


def field_shown(tree: Field, field: Field, document: Any) -> bool:
    """THE ONE VISIBILITY RULE: shown iff MANDATORY or already SET; else hidden (a picker choice)."""
    return is_mandatory(field) or field_set(tree, field, document)


def hidden_options(tree: Field, children, document: Any) -> list[dict]:
    """The picker's choices under a container: the container's OPTIONAL, ABSENT children.

    Each choice is ``{id, key, name, kind, block, field}`` — ``id`` is :func:`view_id` (the row's
    unique handle), ``key`` the schema key printed verbatim, ``name`` the field's dotted name, ``kind``
    the schema's own KIND word (:attr:`Field.kind_label` — the schema carries no prose, so the kind IS
    its one-line description), ``block`` whether it is a block (:data:`BLOCK_KINDS`), and ``field`` the
    :class:`Field` the caller renders as the added control. FIELDS come before BLOCKS.
    """
    options = []
    for child in children:
        if field_shown(tree, child, document):
            continue
        # a FIXED-ALTERNATIVE container's own key is ``regex;(frontend|client)`` — unreadable. Its
        # display name is the schema's own list, joined, so the picker reads "frontend | client".
        key = " | ".join(child.alternatives) if child.kind == KIND_REGEX and child.alternatives \
            else child.key
        options.append({"id": view_id(child), "key": key, "name": child.name,
                        "kind": child.kind_label, "block": child.kind in BLOCK_KINDS,
                        "field": child})
    options.sort(key=lambda option: option["block"])   # stable: fields (False) first, blocks after
    return options


def visibility(tree: Field, container: Field, document: Any) -> dict:
    """The SHOW/HIDE model for ONE rendered container — what to hide, the pickers, what is removable.

    Returns ``{picks, hidden, removable}``:

    * ``picks`` maps a container's ``name`` (``""`` for *container* itself) to its
      :func:`hidden_options` — the choices its ``+ Add`` offers;
    * ``hidden`` is the set of :func:`view_id`s of every hidden field, so the template SKIPS it (it is
      offered by a picker instead);
    * ``removable`` is the set of :func:`view_id`s of every field that is OPTIONAL and SET — the only
      fields a trashcan removes. A MANDATORY field is never removable (removing it would break the
      schema), and an ABSENT one has nothing to remove.

    Recurses into every nested map, so a block's own picker and its own removables are in the SAME
    model — one pass, one answer.
    """
    picks: dict[str, list[dict]] = {}
    hidden: set[str] = set()
    removable: set[str] = set()

    def walk(name: str, children) -> None:
        options = hidden_options(tree, children, document)
        if options:
            picks[name] = options
            hidden.update(option["id"] for option in options)
        for child in children:
            if child.kind == KIND_MAP:
                walk(child.name, child.children)
            # A ``regex;`` container is never REMOVABLE here: it has no key of its own (its entries are
            # its parent's keys), so "deleting the field" would delete the WHOLE parent mapping. Its
            # entries are removed by the container's OWN per-entry ``__entries`` control, not a picker.
            if child.kind != KIND_REGEX and not is_mandatory(child) \
                    and field_shown(tree, child, document):
                removable.add(view_id(child))

    walk("", container.children)
    return {"picks": picks, "hidden": frozenset(hidden), "removable": frozenset(removable)}


#: The companion a form posts to name the fields/blocks the operator REMOVED — newline-joined ENCODED
#: paths (:func:`encode_path`). REMOVAL IS ALWAYS EXPLICIT: a field the form merely does not state is
#: "unchanged", NEVER a deletion, so the only way a save takes a value away is the operator naming it
#: here. ``apply_form`` REFUSES a path the tree cannot place and a path naming a MANDATORY field.
REMOVED_COMPANION = "__remove"

#: The companion the removal ACKNOWLEDGEMENT rides in. The save route (not this module) validates it:
#: a value-carrying removal must present the acknowledgement the render minted (bound to the file and
#: its revision), so a bare POST that never rendered the page cannot delete anything. It is skipped by
#: the leaf pass like every other companion — it is not a document path.
REMOVE_ACK_COMPANION = "__remove_ack"

#: The companions a generated form posts that are NOT document paths — the leaf pass skips them. The
#: set itself is declared with the OTHER companions, below (it names ``KEY_COMPANION``/``ENTRIES_COMPANION``,
#: which are declared there).


def encode_path(tokens: tuple) -> str:
    """A field path for the wire: each token :func:`encode_token`-escaped, joined with ``.``.

    The inverse of :func:`decode_path`. The two characters a token must not carry on the wire (``%``
    and ``.``) are escaped per token, exactly as a form field name escapes them, so a path with an
    operator's free-text key in it (:func:`encode_token`) round-trips.
    """
    return ".".join(encode_token(token) for token in tokens)


def decode_path(text: str) -> tuple:
    """The inverse of :func:`encode_path` — a posted removal path back to its document tokens."""
    return tuple(decode_token(token) for token in str(text).split(".") if token != "")


# ------------------------------------------------------------------ the document

def read_document(path: str | Path) -> Any:
    """Load the config file with the round-trip loader, or ``None`` when it does not exist.

    Imported lazily from the primitive so this module stays importable without the bot's ``core``
    being fully resident at module import time (the primitive itself is the ONE write path).
    """
    from core.utils.configwrite import load_config_file
    return load_config_file(path)


def value_at(document: Any, path: tuple) -> Any:
    """The current value at *path* in the document, or ``None`` when any leg is missing.

    A ``*`` token in the path means "the first element" — used only when a single value is asked for
    (a list's own value is read at the list's path, without the ``*``).

    A numeric token against a SEQUENCE addresses that row BY INDEX — the exact shape a posted row
    leaf (``f.<seq>.<index>.<rel>``) has. Without this leg a row leaf's CURRENT value read as
    ``None``, so ``apply_form``'s "absent current + empty post = nothing to change" guard swallowed an
    operator CLEARING a row value. ``_path_missing`` already carried this branch; they must agree.
    """
    node = document
    for token in path:
        if token == "*":
            if isinstance(node, (list, CommentedSeq)) and node:
                node = node[0]
            else:
                return None
            continue
        if isinstance(node, dict):
            node = node.get(token)
        elif isinstance(node, list) and token.isdigit():
            node = node[int(token)] if int(token) < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def _path_missing(document: Any, tokens: tuple) -> bool:
    """Whether the document does NOT carry *tokens* yet (a setting the file is missing)."""
    node = document
    for token in tokens:
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return True
    return False


def collect_current(tree: Field, document: Any) -> dict[str, Any]:
    """``{field.name: current value}`` for every leaf — what the template prefills from."""
    values: dict[str, Any] = {}
    for field in leaves(tree):
        if field.kind == KIND_LIST:
            values[field.name] = value_at(document, field.path) or []
        else:
            values[field.name] = value_at(document, field.path)
    return values


# ------------------------------------------------------------------ applying a form

def _coerce(field: Field, text: str) -> Any:
    """The Python value a posted string means for *field* — the schema's own type, never a guess."""
    text = "" if text is None else str(text)
    if field.kind == KIND_BOOL:
        return text.strip().lower() in ("1", "true", "on", "yes")
    if field.kind == KIND_NUMBER:
        stripped = text.strip()
        try:
            return int(stripped)
        except ValueError:
            try:
                return float(stripped)
            except ValueError:
                return stripped          # the schema validation refuses it, visibly
    if field.kind == KIND_LIST:
        element = field.element
        return [_coerce(element, item) for item in text.split("\n") if item != ""] \
            if element is not None else [text]
    return text


def _parse_structure(text: str) -> Any:
    """The STRUCTURE a posted ``func`` value means: the text parsed as YAML (a container's edit form).

    A ``func`` leaf whose current value is a map/list is rendered (and edited) as its YAML — never
    Python's ``repr``, which is not YAML and would be written as a STRING. The save reads it back the
    same way, so an untouched field parses to the SAME structure (the primitive's no-op rule then keeps
    the file byte-identical) while a genuine edit lands. Text that is not valid YAML is left as the raw
    string, which the schema validation on the write then refuses — visibly, never silently.
    """
    text = "" if text is None else str(text)
    if not text.strip():
        return ""
    try:
        from core.utils.configwrite import yaml_roundtrip
        return yaml_roundtrip().load(text)
    except Exception:  # noqa: BLE001 - the schema is the judge, not this parser
        return text


def _container(*, is_list: bool):
    return CommentedSeq() if is_list else CommentedMap()


def _set(document: Any, tokens: tuple, value: Any) -> None:
    """Set *value* at *tokens* in *document*, creating intermediate maps/lists as needed.

    A token that is all digits addresses a LIST element (a sequence row); any other token addresses a
    MAP key (a regex entry's free-text key, or an ordinary key). Creating an intermediate container
    uses the NEXT token's shape, so a sequence of maps materialises as a list of maps, never a map.

    A list index is only ever APPENDED to or overwritten, never JUMPED past: a position beyond the
    current end is a backstop no-op. A contiguous add lands on ``position == len`` and appends one row
    (``apply_form`` validates the posted indexes against ``..__n`` first — see
    :func:`_validate_seq_rows`), so this guard can only ever fire on a forged field a validation the
    caller skipped — it stops such a field inventing the empty rows between the end and its index.
    """
    node = document
    for index, token in enumerate(tokens):
        if index == len(tokens) - 1:
            if isinstance(node, list) and token.isdigit():
                position = int(token)
                if position > len(node):
                    return
                while len(node) <= position:
                    node.append(CommentedMap())
                node[position] = value
            elif isinstance(node, dict):
                node[token] = value
            return
        nxt = tokens[index + 1]
        if isinstance(node, list) and token.isdigit():
            position = int(token)
            if position > len(node):
                return
            while len(node) <= position:
                node.append(CommentedMap())
            child = node[position]
            if not isinstance(child, (dict, list)):
                child = _container(is_list=nxt.isdigit())
                node[position] = child
        elif isinstance(node, dict):
            child = node.get(token)
            if not isinstance(child, (dict, list)):
                child = _container(is_list=nxt.isdigit())
                node[token] = child
        else:
            return
        node = child


def _stated(form) -> dict[str, list[str]]:
    """Every posted ``f.`` field, as ``{path: [values in order]}`` (last value wins for scalars)."""
    out: dict[str, list[str]] = {}
    multi = getattr(form, "multi_items", None)
    items: list | None = None
    if callable(multi):
        try:
            items = list(multi())
        except Exception:  # noqa: BLE001 - a plain-dict double has no multi_items
            items = None
    if items is None:
        items = [(key, form[key]) for key in form] if hasattr(form, "__iter__") else []
    for key, value in items:
        if isinstance(key, str) and key.startswith("f."):
            out.setdefault(key[2:], []).append(value)
    return out


class UnknownFieldError(ValueError):
    """A posted field the schema tree cannot resolve.

    Raised by :func:`apply_form` instead of silently skipping the field: a save that cannot place a
    field would DROP it while the page still reported success, which is the one failure this page must
    never have. The save route turns this into a visible refusal (nothing is written).
    """

    def __init__(self, name: str):
        super().__init__(f"the form posted a field the schema cannot place: {name!r}")
        self.name = name


class DuplicateEntryError(ValueError):
    """A NEW custom entry whose key already names an existing entry — refused, never a silent overwrite.

    A ``regex;(.+)`` entry is keyed by the operator's own free text. A NEW entry whose key equals an
    existing entry's would CLOBBER that entry (data loss) while the page still reported success — the
    same "reported success while dropping data" failure class as :class:`UnknownFieldError`. Raised by
    :func:`apply_form`; the save route turns it into a visible refusal and writes nothing, so the
    operator renames the new entry instead of losing the old one.
    """

    def __init__(self, key: str):
        super().__init__(f"a new entry would overwrite the existing entry {key!r}")
        self.key = key


#: The companion a regex container posts to NAME the entries it RENDERED — the encoded keys, newline
#: joined. A key that is rendered but carries no posted leaf any more is one the operator REMOVED; a
#: posted leaf with no rendered key is one they ADDED. Without this the save cannot tell a removed
#: entry from one that was never rendered (a removed row posts nothing at all).
ENTRIES_COMPANION = "__entries"
#: The companion a regex entry echoes its own key in, and a NEW row's editable key control carries.
#: Both are skipped by the leaf pass: an entry's identity comes from its path token, never the echo.
KEY_COMPANION = "__key"

#: EVERY companion a generated form posts that is NOT a document path — the leaf pass skips them all.
#: Declared here (with the companions it names) so a new one is added in ONE place.
_COMPANIONS = frozenset({"__n", KEY_COMPANION, ENTRIES_COMPANION, REMOVED_COMPANION,
                         REMOVE_ACK_COMPANION})


#: The two characters a single path TOKEN must never carry on the wire, each with its escape. Order
#: matters: ``%`` is escaped first, so a literal ``%2E`` in a key becomes ``%252E`` and cannot be
#: mistaken for an encoded dot when decoding.
_TOKEN_ESCAPES = (("%", "%25"), (".", "%2E"))


def encode_token(token: str) -> str:
    """Escape the two characters a path token must not carry (``%``, then ``.``).

    A form field name is ``f.`` + a field's dotted path. A REGEX entry's key is FREE TEXT the operator
    invents, so it may itself contain a ``.`` (or a ``%``) — which would split into two tokens and make
    the field unresolvable, a silent drop on save. Escaping the two characters makes a token opaque to
    the path separator and the escape itself, so the split is unambiguous. Schema keys carry neither
    character, so this is an IDENTITY for every ordinary field — it bites only on an operator's key.
    """
    text = str(token)
    for char, code in _TOKEN_ESCAPES:
        text = text.replace(char, code)
    return text


def decode_token(token: str) -> str:
    """The inverse of :func:`encode_token` — unescape ``%2E`` BEFORE ``%25`` so the pair round-trips.

    Decoding in the reverse order of encoding is what keeps the pair a true bijection:
    ``decode_token(encode_token(t)) == t`` for every ``t`` (including a key that already contains a
    literal ``%2E``).
    """
    return str(token).replace("%2E", ".").replace("%25", "%")


def _decode_tokens(tokens: tuple) -> tuple:
    """Decode every token of a posted dotted path — the exact inverse of ``encode_token`` per token."""
    return tuple(decode_token(token) for token in tokens)


def find_field(tree: Field, tokens: tuple) -> Field | None:
    """The :class:`Field` a posted dotted path names, matching the tree's variable tokens.

    A literal token matches a child by key; a regex child (whose keys the operator invents) matches
    ANY token; a sequence's element matches its index token. Literal matches are tried before regex
    ones, so an operator entry can never shadow a real key. ``None`` when nothing matches — an
    unexpected field is ignored, never guessed at.
    """
    if not tokens:
        return tree
    head, rest = tokens[0], tokens[1:]
    for child in tree.children:
        if child.kind != KIND_REGEX and child.key == head:
            found = find_field(child, rest)
            if found is not None:
                return found
    for child in tree.children:
        if child.kind == KIND_REGEX and child.element is not None:
            found = find_field(child.element, rest)
            if found is not None:
                return found
    if tree.kind == KIND_CHOICE:
        for child in tree.children:
            found = find_field(child, tokens)
            if found is not None:
                return found
    if tree.kind in (KIND_LIST, KIND_SEQ, KIND_REGEX) and tree.element is not None:
        # Descend into the element ONLY when it is itself a structured type (a map, a nested
        # seq/regex, a layout chooser). A SCALAR element is addressed by the collection's OWN name
        # (``f.<list>`` repeated), never by ``f.<list>.<token>`` — and descending into it would make
        # the collection match ANY token, which is what let a layout chooser's LIST option shadow its
        # MAP option (``schemas/presets_schema.yaml``): ``f.<entry>.modify.debug`` resolved to the
        # list's scalar element, so the value was written as a STRING and the save was refused.
        if tree.element.kind in (KIND_MAP, KIND_SEQ, KIND_REGEX, KIND_CHOICE):
            return find_field(tree.element, rest)
        return None
    return None


def _custom_entry_context(tree: Field, stated: dict[str, list[str]]) -> list[tuple]:
    """Per regex container a save touches, what the form said about its entries.

    Returns ``(field, rendered, surviving, new_keys, covered)`` per container that posted its
    ``__entries`` companion:

    * ``rendered`` — the ENCODED keys the page said it RENDERED (the ``__entries`` companion, newline
      joined). A rendered key that no longer carries a posted leaf is one the operator REMOVED;
    * ``surviving`` — the encoded keys whose ``__key`` echo IS still posted (an existing entry's
      readonly key control; a removed row posts nothing, so its key drops out);
    * ``new_keys`` — the RAW keys a NEW row's own key control posted (``f.<base>.__key``). A new row's
      sub-fields are renamed to those keys by ``configgen.js``; the control itself keeps ``__key``;
    * ``covered`` — the ENCODED keys the container's mapping covers by NAME (its literal siblings,
      re-derived SERVER-SIDE from the schema tree by :func:`_literal_sibling_keys` — the SAME filter
      the render path applies). These are the keys that are never an operator entry, so a forged or
      stale ``__entries`` naming one (or ``DEFAULT``) must never delete it.

    A ``__entries`` companion the tree cannot place is REFUSED (:class:`UnknownFieldError`), like every
    other stated field — never silently ignored.
    """
    out: list[tuple] = []
    for name, values in stated.items():
        raw = tuple(name.split("."))
        if not raw or raw[-1] != ENTRIES_COMPANION:
            continue
        base = _decode_tokens(raw[:-1])
        field = next((f for f in flatten(tree) if f.kind == KIND_REGEX and f.path == base), None)
        if field is None:
            raise UnknownFieldError(name)
        covered = frozenset(encode_token(key) for key in _literal_sibling_keys(tree, field))
        base_raw = raw[:-1]
        rendered = [token for token in "".join(str(v) for v in values).split("\n") if token != ""]
        surviving = set()
        for other in stated:
            oraw = tuple(other.split("."))
            if len(oraw) == len(base_raw) + 2 and oraw[:-2] == base_raw \
                    and oraw[-1] == KEY_COMPANION:
                surviving.add(oraw[-2])
        key_control = ".".join(base_raw + (KEY_COMPANION,))
        new_keys = [str(v).strip() for v in stated.get(key_control, []) if str(v).strip()]
        # A FIXED-ALTERNATIVE container accepts ONLY its schema-named sections as new keys: the page
        # offers exactly those, so a posted key outside the list is forged or stale and is REFUSED
        # (nothing written) rather than adding a key the schema does not name.
        if field.alternatives:
            allowed = set(field.alternatives)
            for key in new_keys:
                if key not in allowed:
                    raise UnknownFieldError(name)
        out.append((field, rendered, surviving, new_keys, covered))
    return out


def _refuse_custom_entry_collisions(context: list[tuple]) -> None:
    """Refuse a NEW entry whose key already names a SURVIVING entry, or repeats another NEW one.

    A new entry is keyed by the operator's free text; a key equal to an existing entry's would
    overwrite it — silent data loss. A new key matching a rendered-but-REMOVED entry is allowed (that
    is a delete-and-recreate of the operator's own entry, no surviving value is lost).
    """
    for _field, _rendered, surviving, new_keys, _covered in context:
        seen: set[str] = set()
        for key in new_keys:
            encoded = encode_token(key)
            if encoded in surviving or encoded in seen:
                raise DuplicateEntryError(key)
            seen.add(encoded)


def _delete_removed_custom_entries(document: Any, context: list[tuple]) -> None:
    """Delete every entry the page RENDERED but the form no longer carries a leaf for.

    A removed entry posts neither its ``__key`` echo nor any sub-field, so the rendered set is the
    only thing that can tell "removed" from "never existed". A key a NEW entry re-creates is left for
    the leaf pass (it is not deleted here), and a key the mapping already covers by NAME is never in
    ``rendered`` (``regex_value`` excludes it).

    The DELETABLE set is re-derived SERVER-SIDE from the schema tree, not taken from the post: a key
    the mapping covers by NAME (``covered`` — the container's literal siblings, or ``DEFAULT`` for a
    top-level regex) is never an operator entry, so a forged or stale ``__entries`` naming one deletes
    NOTHING. The client's list is INTERSECTED with what the schema says is deletable, never trusted.
    """
    for field, rendered, surviving, new_keys, covered in context:
        parent = value_at(document, field.path)
        if not isinstance(parent, dict):
            continue
        recreated = {encode_token(key) for key in new_keys}
        allowed = {encode_token(name) for name in field.alternatives} if field.alternatives else None
        for encoded in rendered:
            if encoded in covered:
                continue                # the schema covers this key by NAME — never an operator entry
            if allowed is not None and encoded not in allowed:
                # a FIXED-ALTERNATIVE container deletes ONLY its own allowed names: a key outside the
                # list belongs to a sibling container, so a forged or stale ``__entries`` naming one
                # must never delete it.
                continue
            if encoded in surviving or encoded in recreated:
                continue
            key = decode_token(encoded)
            if key in parent:
                del parent[key]


def _delete_removed_fields(document: Any, tree: Field, stated: dict[str, list[str]]) -> None:
    """Delete every field/block the form NAMED as removed (the :data:`REMOVED_COMPANION` list).

    The posted values are newline-joined ENCODED paths (:func:`encode_path`); each is decoded and
    resolved against the tree. An UNPLACEABLE path, or one naming a MANDATORY field, is a
    :class:`UnknownFieldError` — the whole save is REFUSED with nothing written — so a crafted or stale
    post can never delete a key the tree does not carry or a field the schema makes required. A path
    that resolves but is absent from the document is a harmless no-op (the operator removed a field that
    was not there, which changes nothing).
    """
    for text in (line for value in stated.get(REMOVED_COMPANION, [])
                 for line in str(value or "").split("\n") if line != ""):
        tokens = decode_path(text)
        field = find_field(tree, tokens)
        if field is None or is_mandatory(field):
            raise UnknownFieldError(text)
        current = document
        for token in tokens[:-1]:
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
                current = current[int(token)]
            else:
                current = None
                break
        if current is None:
            continue
        last = tokens[-1]
        if isinstance(current, dict) and last in current:
            del current[last]
        elif isinstance(current, list) and last.isdigit() and int(last) < len(current):
            del current[int(last)]


def _validate_seq_rows(tree: Field, stated: dict[str, list[str]]) -> None:
    """Refuse a posted sequence index the client invented — outside the declared ``0..__n-1``.

    A sequence states its NEW LENGTH separately (``f.<path>.__n``); every row's leaf is named
    ``f.<path>.<index>.<rel>``. The page ALWAYS posts a contiguous ``0..__n-1`` set (``configgen.js``
    renumbers rows after a removal and the counter follows the row count), so a post whose row indexes
    are not exactly ``{0, ..., __n-1}`` is forged or stale. The two ways that happen are the ones the
    reviewer named: an INDEX outside the range, and a ``__n`` LARGER than the posted rows. Both would
    let ``_set`` MATERIALISE empty ``CommentedMap`` rows — junk on disk for a schema whose row fields
    are nullable — so both are refused here, BEFORE any mutation, as an :class:`UnknownFieldError`.

    A sequence whose element carries NO posted leaf cannot have a row forged through a leaf, so it is
    left alone; a non-numeric ``__n`` is handled by the truncation pass, not here.
    """
    for name, values in stated.items():
        if not name.endswith(".__n"):
            continue
        base_raw = tuple(name[:-4].split("."))
        field = find_field(tree, _decode_tokens(base_raw))
        if field is None:
            raise UnknownFieldError(name)
        if field.kind != KIND_SEQ or not form_leaves(field):
            continue
        try:
            length = max(0, int(values[-1]))
        except (TypeError, ValueError):
            continue
        indexes = set()
        for other in stated:
            oraw = tuple(other.split("."))
            if len(oraw) > len(base_raw) and oraw[:len(base_raw)] == base_raw \
                    and oraw[len(base_raw)].isdigit():
                indexes.add(int(oraw[len(base_raw)]))
        if indexes != set(range(length)):
            raise UnknownFieldError(name)


def apply_form(document: Any, form, tree: Field) -> None:
    """Mutate *document* in place from a submitted *form*, using the schema's own types.

    Only fields the form STATES are applied (a bool always states itself via its companion field), so
    an untouched file is left byte-for-byte and the primitive's no-op rule keeps the common case
    silent. A value is coerced by the :class:`Field` that DECLARED it — the same tree the page
    rendered from — so the form and the write cannot disagree about a field's type.

    A sequence of maps states its NEW LENGTH separately (``f.<path>.__n``), so a row the operator
    removed is truncated off the end before the rows that remain are written — a removal is a
    genuine change the no-op rule must see, not a silently shorter iteration. The posted row indexes
    are checked against that length FIRST (:func:`_validate_seq_rows`): an index outside ``0..__n-1``,
    or a ``__n`` larger than the posted rows, is REFUSED rather than allowed to materialise an empty
    row the client invented.

    A regex container states the ENTRIES it rendered (``f.<path>.__entries``), so the save can DELETE
    an entry the operator removed (see :func:`_delete_removed_custom_entries`) and REFUSE a new entry
    whose key would clobber a surviving one (:class:`DuplicateEntryError`, see
    :func:`_refuse_custom_entry_collisions`) — neither silently drops or overwrites data.

    Every token is DECODED before it is matched against the tree (see :func:`encode_token`), so a field
    whose path carries an operator's free-text key — a key that may contain ``.``, a space, ``[`` or
    ``]`` — resolves to the same document path the page rendered it from. And a field the tree cannot
    place is REFUSED (:class:`UnknownFieldError`), never skipped: a save must never report success while
    dropping a field. The companions that are NOT document paths are skipped by design — a trailing
    ``__key`` (a regex entry's key echo / a new row's key control), ``__entries`` (the rendered-entry
    list) and ``__n`` (handled in its own pass).
    """
    stated = _stated(form)
    _validate_seq_rows(tree, stated)
    entry_context = _custom_entry_context(tree, stated)
    _refuse_custom_entry_collisions(entry_context)
    _delete_removed_custom_entries(document, entry_context)
    for name, values in stated.items():
        if not name.endswith(".__n"):
            continue
        tokens = _decode_tokens(tuple(name[:-4].split(".")))
        field = find_field(tree, tokens)
        if field is None:
            raise UnknownFieldError(name)
        try:
            length = max(0, int(values[-1]))
        except (TypeError, ValueError):
            continue
        current = document
        for token in tokens[:-1]:
            current = current.get(token) if isinstance(current, dict) else None
            if current is None:
                break
        last = tokens[-1]
        if isinstance(current, dict) and isinstance(current.get(last), list):
            del current[last][length:]
    for name, values in stated.items():
        if name.endswith(".__n"):
            continue
        raw_tokens = tuple(name.split("."))
        if raw_tokens and raw_tokens[-1] in _COMPANIONS:
            continue                       # a regex entry's key echo / key control / rendered entries
                                           # / the removal list / the removal acknowledgement
        tokens = _decode_tokens(raw_tokens)
        field = find_field(tree, tokens)
        if field is None:
            raise UnknownFieldError(name)
        current = value_at(document, tokens)
        if field.kind == KIND_LIST:
            element = field.element
            items = [item for value in values for item in str(value or "").split("\n") if item != ""]
            new_value = [_coerce(element, item) for item in items] if element else list(values)
        else:
            posted = values[-1] if values else ""
            # an ABSENT current and an EMPTY post is "nothing to change": the page renders a setting
            # the file does not carry yet, and merely showing it must not CREATE it.
            if current is None and str(posted).strip() == "":
                continue
            if field.kind == KIND_FUNC and isinstance(current, (dict, list)):
                # a ``func`` leaf whose value is a CONTAINER is shown (and edited) as YAML, so it is
                # read back the same way — an untouched field parses to the SAME structure (the no-op
                # rule then keeps the file byte-identical) and a genuine edit lands.
                new_value = _parse_structure(posted)
            else:
                new_value = _coerce(field, posted)
        # A key the file does NOT carry yet is not CREATED by merely showing it: only a value the
        # operator actually moved (differs from the field's default, or a boolean set true) writes.
        if _path_missing(document, tokens):
            if new_value in (None, "") or new_value == field.default:
                continue
            if field.kind == KIND_BOOL and new_value is False:
                continue
        # only a GENUINE change is written: an unchanged field is skipped here rather than left for
        # the primitive's no-op rule, because setting an equal value can still reformat a hand file
        # (a multi-line plain scalar re-emits on one line) — the "only the intended line changes"
        # promise depends on not touching a field the operator did not move.
        if current == new_value:
            continue
        _set(document, tokens, new_value)
    # THE REMOVALS LAST, so they are AUTHORITATIVE: a form that names a field as removed deletes it
    # whatever else it posted (a stale page that still carries the field's leaf, a browser whose script
    # did not hide it) — the operator's removal is never undone by the same save. Unplaceable paths and
    # MANDATORY fields are refused here (nothing is written: the primitive aborts before the write).
    _delete_removed_fields(document, tree, stated)
