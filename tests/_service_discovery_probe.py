"""Recorder probe: which module names does ``dynamic_import('services')`` request?

Extracts the REAL body of ``dynamic_import`` from ``core/utils/helper.py`` (that module cannot be
imported in the test interpreter -- it needs luadata/lupa, ``Port`` and croniter) and replaces
``importlib.import_module`` with a recorder that returns a throwaway module for every name except
the initial ``services`` package, so the discovery walk is observed without executing the services.

Emits one JSON line ``{"yielded": [...], "requested": [...], "unimportable": [...]}``; ``yielded``
vs ``requested`` is the property under test (discovery must request every module the walk yields).

Usage: ``python tests/_service_discovery_probe.py``"""
from __future__ import annotations

import ast
import importlib
import json
import pkgutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HELPER_PATH = ROOT / "core" / "utils" / "helper.py"
FUNCTION_NAME = "dynamic_import"


class _RecordingLogger:
    """Stands in for helper.py's module logger; records what the walker reported."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:  # pragma: no cover - not used by the walker
        self.messages.append(message)


def _load_dynamic_import() -> types.ModuleType:
    """Extract the real ``dynamic_import`` out of helper.py and exec it in a minimal namespace.

    Only ``importlib``, ``pkgutil`` and ``logger`` are referenced by the body, so this needs no
    third-party import -- which is the point: the probe must run on the test interpreter.
    """
    source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == FUNCTION_NAME), None)
    if node is None:  # pragma: no cover - loud, so a rename cannot silently pass
        raise SystemExit(f"core/utils/helper.py no longer defines {FUNCTION_NAME}()")
    body_source = ast.get_source_segment(source, node) or ""
    if "walk_packages" not in body_source:  # pragma: no cover - guards against extracting a stub
        raise SystemExit(f"{FUNCTION_NAME}() no longer walks packages: {body_source!r}")

    namespace: dict = {
        "importlib": importlib,
        "pkgutil": pkgutil,
        "logger": _RecordingLogger(),
    }
    module = types.ModuleType("helper_dynamic_import_under_test")
    module.__dict__.update(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(HELPER_PATH), "exec"),
         module.__dict__)
    return module


def main() -> int:
    helper = _load_dynamic_import()

    import services  # noqa: F401 - resolved from the repo root on sys.path

    yielded: list[str] = [
        name for _, name, is_pkg in pkgutil.walk_packages(services.__path__, "services.")
        if not is_pkg
    ]

    unimportable: list[str] = []
    for _, _, _ in pkgutil.walk_packages(services.__path__, "services.",
                                        onerror=unimportable.append):
        pass

    requested: list[str] = []
    real_import_module = importlib.import_module

    def recorder(name, package=None):
        requested.append(name)
        if name == "services":
            return real_import_module(name, package)
        return types.ModuleType(name)          # observe only; never execute a service

    importlib.import_module = recorder
    try:
        helper.dynamic_import("services")
    finally:
        importlib.import_module = real_import_module

    print(json.dumps({"yielded": yielded, "requested": requested,
                      "unimportable": unimportable}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
