"""THE SCHEMA TREE AS COMPLETION HINTS — one JSON document per config file, for the Expert Mode editor.

WHAT IT SERVES
--------------
The payload is the schema's own tree, flattened to the one contract a client completes from:

    {"paths": {"<dotted container path>": {"desc": …, "example": …, "kind": …,
                                           "children": [{"key", "path", "kind", "label",
                                                         "required", "desc", "example",
                                                         "enum"}, …]}}}

The payload ROOT is ``""``; a resolved container (:func:`payload`'s ``container`` argument) replaces it,
so a scoped page's document — one node's slice — is described by that node's block. ``children`` is
``[]`` for a leaf, and every child that is a BLOCK (:data:`_CONTAINER_KINDS`) also gets its own
``paths`` entry, so a row can offer the block's description and example.

PATH SEGMENTS. A path is dotted from the payload root, in the order the document nests its keys. One
element of a sequence is ``*`` (``event_filter.*``) and one entry of a ``regex;`` container is ``*``
too (``regex;(.+).*``): neither has a key the schema can write, since the document invents it. A
``regex;`` container's OWN segment is its key as written (``regex;(.+)``), because that is the schema's
key for the block.

THE KEY THE SCHEMAS' LOADER IS THE ONLY SOURCE. The tree comes from :mod:`services.webservice.configgen`
(``load_schema`` then ``describe``), never from a hand-kept key list and never from parsing a schema
file here — so a key, its kind, its description and its enum are exactly what the bot validates
against.

THE LANGUAGE. ``desc`` is schema prose: it is translated through the SCHEMA catalog
(:func:`services.webservice.i18n.schema_text`), which English leaves as the msgid. ``label`` is the
kind word the generated form shows: the webui catalog's, through :func:`i18n.translate`. ``example``
is NEVER translated — it is an illustration, not a sentence.

THE MEMO. The built payload is kept under ``(schema files, container, language, content digest of every
schema file)``. The digest is sha256 of the bytes, never mtime+size: two writes of the same length can
share a clock tick, and a stale hint tree would then describe a schema that has already changed.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from . import configgen, i18n

__all__ = ["payload"]

#: the kinds that expose a child ROW: a row for one gets a ``paths`` entry of its own, carrying that
#: row's description and example. The hybrid kinds (``or_list``/``or_map``) are not: their element is
#: either the value itself or a block, at the SAME document position, so there is no block to open.
_CONTAINER_KINDS = frozenset({configgen.KIND_MAP, configgen.KIND_SEQ, configgen.KIND_LIST,
                              configgen.KIND_REGEX, configgen.KIND_CHOICE})

#: the kinds whose single element (or entry) is a child named by a ``*`` path segment.
_ELEMENT_KINDS = frozenset({configgen.KIND_SEQ, configgen.KIND_LIST, configgen.KIND_REGEX})

#: ``{key: payload}``; the key carries every input, so an entry is never served for another schema.
_CACHE: dict[tuple, dict] = {}
_CACHE_LIMIT = 64
_LOCK = threading.Lock()


def _schema_digest(paths) -> tuple:
    """``(path, sha256)`` per schema file — an edited schema must invalidate the payload.

    Content, not mtime: two writes of the same length can share a clock tick, so an mtime-keyed memo
    could answer a tree for a schema that has already changed.
    """
    digest = []
    for name in paths:
        try:
            data = Path(name).read_bytes()
        except OSError:
            digest.append((str(name), None))
            continue
        digest.append((str(name), hashlib.sha256(data).hexdigest()))
    return tuple(digest)


def _json_value(value):
    """*value* as something ``json.dumps`` accepts: a scalar as-is, a container element by element.

    A schema's ``example`` is arbitrary YAML — a date, a ``Path``, a tuple — and a raw one would make
    the payload unserialisable. Anything that is not JSON-native becomes its ``str()``.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _entries_of(field) -> list[tuple]:
    """``(child, segment)`` per child row of *field* — *segment* is the path segment the row adds.

    A chooser's option blocks hold the SAME document position as the chooser itself: their keys are the
    document's keys there, so they are flattened into the chooser's own rows and the option names never
    appear as rows of their own (an option name is not a key any document carries).
    """
    if field.kind in _ELEMENT_KINDS:
        if field.element is None:
            return []
        return [(field.element, "*")]
    if field.kind == configgen.KIND_CHOICE:
        rows = []
        for option in field.children:
            rows.extend(_entries_of(option))
        return rows
    if field.kind == configgen.KIND_MAP:
        return [(child, str(child.key)) for child in field.children]
    return []


def _resolve(tree: configgen.Field, container) -> configgen.Field | None:
    """The field *container* names, walking the tree's ``children`` by key; ``None`` when it names none.

    A token matching a child's ``key`` descends into that child. ``*`` descends into the element of a
    sequence/regex child. Any other token descends into a ``regex;`` child's element — the first one
    the mapping declares, when it declares several — because that child IS the schema's statement that
    the document invents the key: a node name or an instance name is such a key. A mapping with no
    ``regex;`` child answers ``None``. The caller decides whether a name may be invented at all: a node
    NAME is checked against the keys the FILE carries before this walk is handed anything.
    """
    field = tree
    for raw in container:
        token = str(raw)
        if token == "*":
            found = next((child for child in field.children
                          if child.kind in _ELEMENT_KINDS and child.element is not None), None)
            if found is None:
                return None
            field = found.element
            continue
        found = next((child for child in field.children if str(child.key) == token), None)
        if found is None:
            found = next((child for child in field.children
                          if child.kind == configgen.KIND_REGEX and child.element is not None), None)
            if found is None:
                return None
            field = found.element
            continue
        field = found
    return field


def _desc(field, language: str) -> str:
    """The field's schema prose in *language*; ``""`` when the schema wrote none."""
    return i18n.schema_text(field.desc, language) if field.desc else ""


def _walk(field, path: str, paths: dict, language: str, request, *, root: bool = False) -> None:
    """Add *field* and every container under it to *paths*; an existing entry at *path* is extended.

    Two fields share one path when a chooser's option blocks sit where their parent sits, so their
    children merge into the parent's entry rather than replacing it.
    """
    entry = paths.get(path)
    if entry is None:
        entry = {"desc": "" if root else _desc(field, language),
                 "example": _json_value(field.example),
                 "kind": field.kind,
                 "children": []}
        paths[path] = entry
    for child, segment in _entries_of(field):
        child_path = segment if not path else f"{path}.{segment}"
        entry["children"].append({
            "key": str(child.key),
            "path": child_path,
            "kind": child.kind,
            "label": i18n.translate(request, child.kind_label),
            "required": bool(child.required),
            "desc": _desc(child, language),
            "example": _json_value(child.example),
            "enum": [_json_value(value) for value in child.enum],
        })
        if child.kind in _CONTAINER_KINDS:
            _walk(child, child_path, paths, language, request)


def payload(schema_files, *, container=(), request=None) -> dict:
    """The hint document for *schema_files*, rooted at *container* — the contract in the module docstring.

    ``schema_files[0]`` is the primary schema the tree is walked from; the rest are the siblings its
    ``include:`` partials resolve in (the same list a save validates against). *container* is a tuple
    of keys, resolved inside the tree by :func:`_resolve`; one that cannot be resolved answers
    ``{"paths": {}}`` rather than raising or falling back to the whole document. The returned dict is
    JSON-native and shared with the memo — a caller treats it as read-only.
    """
    files = tuple(str(name) for name in schema_files)
    tokens = tuple(str(token) for token in container)
    if not files:
        return {"paths": {}}
    language = i18n.resolve_language(request)
    key = (files, tokens, language, _schema_digest(files))
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    tree = configgen.describe(configgen.load_schema(files[0], schema_files=list(files)))
    rooted = _resolve(tree, tokens)
    if rooted is None:
        built: dict = {"paths": {}}
    else:
        paths: dict = {}
        _walk(rooted, "", paths, language, request, root=True)
        built = {"paths": paths}
    with _LOCK:
        _store(key, built)
    return built


def _store(key: tuple, built: dict) -> None:
    """Keep *built* under *key*, dropping the oldest half when the map grows past its bound."""
    if len(_CACHE) >= _CACHE_LIMIT:
        for stale in list(_CACHE)[: _CACHE_LIMIT // 2]:
            _CACHE.pop(stale, None)
    _CACHE[key] = built
