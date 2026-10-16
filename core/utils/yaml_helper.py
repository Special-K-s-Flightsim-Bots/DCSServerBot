"""Factory for ruamel ``YAML`` instances: one fresh object per call, never a shared module-level one.

A ruamel ``YAML`` carries per-parse state (reader, scanner, parser, ``doc_infos``) and is not
thread-safe; two threads driving one instance corrupt each other's event stream. ``new_yaml()``
returns a new instance every call so a caller can never share state with another thread.

Typ ``'rt'`` (round-trip) with default style settings: this reproduces exactly what a bare
``YAML()`` produces, so a dump through ``new_yaml()`` is byte-identical to a dump through a plain
module-level ``YAML()``.
"""
from __future__ import annotations

from ruamel.yaml import YAML

__all__ = ["new_yaml"]


def new_yaml() -> YAML:
    """A fresh ``ruamel.yaml.YAML`` with typ ``'rt'`` and no style overrides."""
    return YAML(typ='rt')
