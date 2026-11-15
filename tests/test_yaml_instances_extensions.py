"""The fresh-per-call YAML rule: no shared module-level ``YAML()`` under ``extensions/``.

A ruamel ``YAML`` carries per-parse state (reader, scanner, parser, ``doc_infos``) and is not
thread-safe. ``Server.call_extension`` offloads a non-coroutine extension method with
``asyncio.to_thread`` while the loop keeps using the same module's YAML, so a module-level
``yaml = YAML()`` is a live cross-thread pair that corrupts the event stream. The class is closed by
construction: ``core.utils.yaml_helper.new_yaml()`` returns a fresh instance per call.

Three properties, each a test below:

1. **the guard** — a static AST scan asserts NO module-level ``YAML()`` binding remains anywhere
   under ``extensions/`` (``migrate.py`` excluded).
2. **the guard can fail** — positive controls plant such a binding in a scratch module (module
   level, inside a module-level block, and as a class attribute) and assert the detector FLAGS each,
   while staying quiet on legal neighbours (a function-local ``YAML()``, a class attribute holding
   something that is not a ``YAML()``).
3. **output neutrality** — a dump through the OLD pattern (a module-level ``YAML()``) and through
   ``new_yaml()`` is byte-identical, so the sweep changes nothing that is written to disk.
"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils.yaml_helper import new_yaml  # noqa: E402
from ruamel.yaml import YAML  # noqa: E402

EXTENSIONS_DIR = REPO_ROOT / "extensions"

#: The OLD pattern exactly as the extension modules had it: ONE instance shared by every caller.
#: Kept here only to prove a dump through it is byte-identical to ``new_yaml()``.
_OLD_YAML = YAML()


# --------------------------------------------------------------- the detector

def _is_yaml_call(node: ast.AST) -> bool:
    """True for a call that constructs a ruamel YAML: ``YAML(...)`` or ``<mod>.YAML(...)``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "YAML"
    if isinstance(func, ast.Attribute):
        return func.attr == "YAML"
    return False


def _module_level_yaml_bindings(source: str) -> list[tuple[int, str]]:
    """AST scan for a NAME bound to a ``YAML()`` call at MODULE level.

    Module level means: reachable from the module body, including inside a module-level ``if`` /
    ``try`` / ``with`` / loop, AND inside a module-level ``class`` body (a class attribute is one
    object shared by every caller too). A binding inside a FUNCTION or METHOD body is legal — the
    instance is per-call and dies with the frame — so it is not reported.

    Returns ``(lineno, name)`` for each offending binding.
    """
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []

    def names_in_target(target: ast.expr) -> list[tuple[int, str]]:
        if isinstance(target, ast.Name):
            return [(target.lineno, target.id)]
        if isinstance(target, (ast.Tuple, ast.List)):
            out: list[tuple[int, str]] = []
            for elt in target.elts:
                out.extend(names_in_target(elt))
            return out
        return []

    def scan(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, ast.Assign):
                if _is_yaml_call(stmt.value):
                    for lineno, name in names_in_target(stmt.targets[0]):
                        found.append((lineno, name))
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is not None and _is_yaml_call(stmt.value):
                    found.extend(names_in_target(stmt.target))
            elif isinstance(stmt, ast.ClassDef):
                # a class BODY is module-level state (one attribute object, shared); method bodies
                # are not, and ``scan`` stops at FunctionDef below.
                scan(stmt.body)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # a binding inside a function/method body is not module-level; do not descend
                continue
            elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                                   ast.AsyncWith, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(stmt, field, None)
                    if nested:
                        scan(nested)
                for handler in getattr(stmt, "handlers", []):
                    scan(handler.body)

    scan(tree.body)
    return found


def _offenders_in_file(path: Path) -> list[tuple[int, str]]:
    return _module_level_yaml_bindings(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- 1. the guard

def test_no_module_level_yaml_binding_under_extensions():
    """No module-level ``YAML()`` binding remains anywhere under ``extensions/`` (``migrate.py``
    excluded). A failure names every offending file, line and binding."""
    assert EXTENSIONS_DIR.is_dir(), f"expected the extensions package at {EXTENSIONS_DIR}"

    scanned = [p for p in EXTENSIONS_DIR.rglob("*.py") if p.name != "migrate.py"]
    assert scanned, "the scan must have actually looked at extension sources"

    offenders: list[str] = []
    for path in scanned:
        for lineno, name in _offenders_in_file(path):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}: {name} = YAML()")

    assert not offenders, (
        "module-level YAML() bindings are shared across threads; use "
        "core.utils.yaml_helper.new_yaml() per call instead:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_reaches_every_extension_source():
    """The guard is only meaningful if it walks the whole tree: it must see the three modules that
    used to carry the binding, by path."""
    scanned = {p.relative_to(REPO_ROOT).as_posix()
               for p in EXTENSIONS_DIR.rglob("*.py") if p.name != "migrate.py"}
    for expected in ("extensions/mizedit/extension.py",
                     "extensions/skyeye/extension.py",
                     "extensions/cloud/extension.py"):
        assert expected in scanned


# --------------------------------------------------------------- 2. the positive control

def test_the_detector_flags_a_planted_module_level_binding():
    """POSITIVE CONTROL: the exact shape the guard exists to catch is flagged."""
    planted = "from ruamel.yaml import YAML\nyaml = YAML()\n"
    assert _module_level_yaml_bindings(planted) == [(2, "yaml")]


def test_the_detector_flags_a_planted_binding_in_a_module_level_block():
    """A binding inside a module-level ``if``/``try`` is still shared module state: also flagged."""
    planted = (
        "from ruamel.yaml import YAML\n"
        "try:\n"
        "    yaml = YAML()\n"
        "except ImportError:\n"
        "    yaml = None\n"
    )
    assert _module_level_yaml_bindings(planted) == [(3, "yaml")]


def test_the_detector_flags_a_class_attribute_binding():
    """POSITIVE CONTROL: a ``yaml = YAML()`` planted as a CLASS ATTRIBUTE is one object shared by
    every instance and caller, so it is flagged exactly like a module-level binding."""
    planted = (
        "from ruamel.yaml import YAML\n"
        "class Loader:\n"
        "    yaml = YAML()\n"
    )
    assert _module_level_yaml_bindings(planted) == [(3, "yaml")]


def test_the_detector_stays_quiet_on_a_class_attribute_holding_something_else():
    """NEGATIVE CONTROL for the class-attribute shape: a class attribute that is NOT a ``YAML()``
    call must NOT be flagged, so the control above cannot pass by flagging every class attribute."""
    planted = (
        "from ruamel.yaml import YAML\n"
        "class Loader:\n"
        "    label = \"yaml\"\n"
        "    typ = 'rt'\n"
    )
    assert _module_level_yaml_bindings(planted) == []


def test_the_detector_stays_quiet_on_a_legal_function_local_binding():
    """LEGAL NEIGHBOUR: a ``YAML()`` bound inside a function is per-call and must NOT be reported."""
    legal = (
        "from ruamel.yaml import YAML\n"
        "def load(text):\n"
        "    yaml = YAML()\n"
        "    return yaml.load(text)\n"
    )
    assert _module_level_yaml_bindings(legal) == []


def test_the_detector_stays_quiet_on_the_new_pattern():
    """The replacement itself must not trip the guard."""
    new_pattern = "from core.utils.yaml_helper import new_yaml\n"
    assert _module_level_yaml_bindings(new_pattern) == []


# --------------------------------------------------------------- 3. output neutrality

#: A representative document: nested maps, a long plain string, a list of mappings.
REPRESENTATIVE = {
    "root": {
        "nested": {"a": 1, "b": "two", "c": [1, 2, 3]},
        "long": "x" * 200,
        "list": ["alpha", "beta", {"inner": True}],
    },
    "empty": {},
}

#: A comment-bearing round-trip tree: comments, blank lines and quoting must survive a dump.
COMMENTED = """\
# a hand file
root:
  key: value        # trailing comment

  other: 'quoted'
  seq:
    - one
    - two
"""


def _dump_with(yaml, data) -> str:
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def _dump_text_with(yaml, text: str) -> str:
    return _dump_with(yaml, yaml.load(text))


def test_a_plain_document_dumps_byte_identically():
    old = _dump_with(_OLD_YAML, REPRESENTATIVE)
    new = _dump_with(new_yaml(), REPRESENTATIVE)
    assert new == old
    assert new.encode("utf-8") == old.encode("utf-8")


def test_a_comment_bearing_tree_dumps_byte_identically():
    """The round-trip tree keeps its comments: both instances emit the same bytes."""
    old = _dump_text_with(_OLD_YAML, COMMENTED)
    new = _dump_text_with(new_yaml(), COMMENTED)
    assert new == old
    assert "# a hand file" in new and "# trailing comment" in new


def test_new_yaml_returns_a_fresh_instance_every_call():
    """The whole point: two calls never hand back the same object."""
    first = new_yaml()
    second = new_yaml()
    assert first is not second
    assert "rt" in first.typ and "rt" in second.typ
