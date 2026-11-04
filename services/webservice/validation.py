"""THE RENDER'S SCHEMA CHECK, MEMOISED — a config page must not re-validate an unchanged file per view.

WHY THIS EXISTS
---------------
Opening a config page shows a notice when the file on disk does not satisfy its schema, and producing
that notice means the FULL validation: pykwalify parses the schema files (the node schema alone is 21
YAML files) and validates the whole document. Measured on the shipped tree: **~270 ms** for
``nodes.yaml`` — per render, for a banner. Every click on a node paid it again.

So the verdict is kept, keyed by everything it depends on: the file's own sha256, the exact schema
file list, and the mtime+size of every schema file. An edit to the config OR to a schema changes the
key, so a stale verdict can never be shown, and an unchanged page re-validates nothing.

WHAT THIS IS NOT
----------------
It is NOT the write path. A SAVE validates freshly through
:func:`core.utils.configwrite.validate_against_schema` — that call is never memoised, because its
refusal is what protects the file. This module memoises only the check a RENDER runs to decide whether
to draw a notice.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from core.utils import configwrite

__all__ = ["validate_cached"]

#: `{key: None}` for a valid file, `{key: (message, errors, error_path)}` for a refused one; membership
#: is what distinguishes "cached as valid" (None) from "not cached at all".
_CACHE: dict[tuple, tuple | None] = {}
_CACHE_LIMIT = 256
_LOCK = threading.Lock()


def _schema_digest(schemas) -> tuple:
    """``(path, sha256)`` per schema file — an edited schema must invalidate the verdict.

    Content, not mtime: two writes of the same length can share a clock tick (measured), so an
    mtime-keyed memo could answer a verdict for a schema that has already changed.
    """
    digest = []
    for name in schemas:
        try:
            data = Path(name).read_bytes()
        except OSError:
            digest.append((str(name), None))
            continue
        digest.append((str(name), hashlib.sha256(data).hexdigest()))
    return tuple(digest)


def validate_cached(path, schemas) -> None:
    """Validate *path* against *schemas* like a save would, answering from memory when nothing changed.

    Raises :class:`core.utils.configwrite.ConfigValidationError` exactly as the primitive does (the
    same message, ``errors`` and ``error_path``), or returns ``None`` when the file is valid. The file
    itself is re-read every call — that is a few microseconds — so the verdict always describes what is
    on disk right now.
    """
    schemas = list(schemas)
    if not schemas:
        return
    text = configwrite.read_config_text(path)
    key = (str(path), hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(schemas),
           _schema_digest(schemas))
    with _LOCK:
        hit = key in _CACHE
        cached = _CACHE.get(key)
    if hit:
        if cached is None:
            return
        message, errors, error_path = cached
        raise configwrite.ConfigValidationError(message, path=path, schemas=schemas,
                                                errors=errors, error_path=error_path)
    try:
        configwrite.validate_against_schema(text, schemas, path=path)
    except configwrite.ConfigValidationError as ex:
        with _LOCK:
            _store(key, (str(ex), ex.errors, ex.error_path))
        raise
    with _LOCK:
        _store(key, None)


def _store(key: tuple, verdict) -> None:
    """Keep *verdict* under *key*, dropping the oldest half when the map grows past its bound."""
    if len(_CACHE) >= _CACHE_LIMIT:
        for stale in list(_CACHE)[: _CACHE_LIMIT // 2]:
            _CACHE.pop(stale, None)
    _CACHE[key] = verdict
