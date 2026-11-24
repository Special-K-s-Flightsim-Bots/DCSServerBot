"""The direction of the scope rule: ``core`` owns it, the web console consumes it.

``holds_scope`` (with ``check_roles``) is implemented once in ``core/utils/discord.py``; the admin
web UI imports it and defines no rule of its own. Three pins: an AST walk over ``core/**`` for a
module-level ``services`` import (imports inside functions and ``TYPE_CHECKING`` are exempt -- they
never run at import time); a subprocess proving ``core.utils.discord`` is importable alone; and a
source-level check that ``services/webservice/scope.py`` defines none of the three helpers."""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "core"
WEB_SCOPE = REPO_ROOT / "services" / "webservice" / "scope.py"

#: The three names the rule owns. ``ADMIN_ROLE`` and ``server_managed_by`` are NOT here: neither is
#: referenced by any of the three (verified by AST, see the report), so neither has to travel.
MOVED = ("holds_scope", "may_manage_server", "member_scope_tokens")


def _module_level_imports(source: str, filename: str = "<test>") -> list[tuple[int, str]]:
    """``[(lineno, module)]`` for every import that RUNS when *source* is imported.

    Only statements that are direct children of the module body count. An import inside a function
    or inside an ``if TYPE_CHECKING:`` block is executed never/conditionally and is ignored here.
    """
    tree = ast.parse(source, filename=filename)
    found: list[tuple[int, str]] = []
    for statement in tree.body:                      # module body only, by construction
        if isinstance(statement, ast.Import):
            found.extend((statement.lineno, alias.name) for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            found.append((statement.lineno, statement.module or ""))
    return found


# ------------------------------------------------------------------- pin 1 — the static walk


def test_no_core_module_imports_services_at_module_level():
    """Nothing under ``core/`` reaches into ``services`` at import time."""
    files = sorted(CORE.rglob("*.py"))
 # A floor, so a walk that silently finds no files cannot pass.
    assert len(files) > 50, f"expected to walk the whole core tree, found {len(files)} files"

    offenders: list[str] = []
    for path in files:
        for lineno, module in _module_level_imports(path.read_text(encoding="utf-8"), str(path)):
            if module == "services" or module.startswith("services."):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {module}")

    assert not offenders, (
        "core must not import services at module level (the console imports core, so this is a "
        "cycle). The rule lives in core/utils/discord.py; the console consumes it. Move the import "
        "into the function that needs it only if it is genuinely lazy, the way "
        "core/plugin_manager.py's service imports are:\n  " + "\n  ".join(offenders)
    )


def test_the_walk_would_catch_a_module_level_offender():
    """Anti-vacuity: the detector reports a planted module-level import and ignores a nested one."""
    planted = "from services.webservice.scope import holds_scope\n"
    assert [m for _, m in _module_level_imports(planted)] == ["services.webservice.scope"]

    nested = (
        "def f():\n"
        "    from services.webservice.scope import holds_scope\n"
        "    return holds_scope\n"
    )
    assert _module_level_imports(nested) == []


# --------------------------------------------------- pin 2 — the rule is importable from core


def test_the_rule_is_importable_from_core_alone():
    """``import core.utils.discord`` yields the three, and never touches the web scope module.

    ``importlib.import_module`` rather than ``import core.utils.discord as d``: the package
    attribute ``core.utils.discord`` is the THIRD-PARTY ``discord`` module (``core/utils/__init__.py``
    star-imports sibling modules that each ``import discord``), so the ``import a.b as c`` form binds
    the wrong object entirely — a harness trap, not a fact about the rule.
    """
    program = (
        "import importlib, sys\n"
        "d = importlib.import_module('core.utils.discord')\n"
        "names = " + repr(MOVED) + "\n"
        "for name in names:\n"
        "    fn = getattr(d, name)\n"
        "    assert fn.__module__ == 'core.utils.discord', (name, fn.__module__)\n"
        "assert 'services.webservice.scope' not in sys.modules, "
        "'importing core pulled the console scope module in'\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", program], cwd=REPO_ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, (
        "importing the rule from core alone must work:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("ok")


# ------------------------------------------------- pin 3 — the console defines no rule


def test_the_console_defines_no_rule_of_its_own():
    """Source-level: ``services/webservice/scope.py`` imports the three FROM core, defines none."""
    tree = ast.parse(WEB_SCOPE.read_text(encoding="utf-8"), filename=str(WEB_SCOPE))

    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not (set(MOVED) & defined), (
        f"the console must not restate the rule; it still defines {sorted(set(MOVED) & defined)}"
    )

    imported_from_core: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "core.utils.discord":
            imported_from_core.update(alias.name for alias in node.names)

    missing = sorted(set(MOVED) - imported_from_core)
    assert not missing, (
        f"services/webservice/scope.py must import {missing} from core.utils.discord "
        f"(it imports {sorted(imported_from_core)})"
    )
