"""Tolerant attribute access, shared by the read models.

The source hands the models live objects (a ``ServerImpl``, a ``NodeProxy``, a ``Mission``) in
production and ``SimpleNamespace`` stand-ins in tests. Two rules make both safe:

* a value that is missing, ``None`` or of the wrong type is reported as *absent* (``None``), never
  guessed — an invented number on a status page is worse than a blank one;
* a PROPERTY that raises is caught: the domain objects expose properties that call ``self.log``,
  read the pool or raise ``FatalException`` (``Instance.dcs_port``), and a status page must not
  500 because one object is half-initialised.

Nothing here imports ``core``.
"""
from __future__ import annotations

__all__ = ["text", "as_int", "port_number", "as_bool", "attr", "safe"]


def attr(obj, name: str, default=None):
    """``getattr`` that treats a raising property like a missing attribute."""
    if obj is None:
        return default
    try:
        value = getattr(obj, name, default)
    except Exception:
        return default
    return default if value is None else value


def safe(fn, default=None):
    """Call *fn*, returning *default* for any exception (see the module docstring)."""
    try:
        return fn()
    except Exception:
        return default


def text(value) -> str:
    """A trimmed string, or ``''`` — never ``None`` and never ``'None'``."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value):
    """An int, or ``None`` when the value is absent or not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_bool(value) -> bool:
    return bool(value) and value is not None


def port_number(value):
    """The port number of a ``Port`` (which defines ``__int__``/``__index__``), or an int itself."""
    if value is None:
        return None
    direct = as_int(getattr(value, "port", None))
    if direct is not None:
        return direct
    return as_int(value)