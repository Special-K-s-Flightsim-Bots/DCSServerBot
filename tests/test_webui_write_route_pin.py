"""A mechanical pin on the write seam's authorization half.

``call_action`` executes any registered action for whoever reaches it, so the capability gate is the
ONLY authorization a console write has. The registrar refuses an UNKNOWN capability on a DECLARED
route, but a module that simply forgets to declare one is SILENT: the route ships, nothing notices, and
the suite stays green. This is a cheap structural pin, in the same shape as the read-side
``request_source`` pin: an AST walk over the write seam's own package asserting that every module which
CALLS ``call_action`` also DECLARES a capability. It is per MODULE, not per route; it reads the
DECLARATION, not its value (an explicitly EMPTY literal does not count); and it scans
``services/webservice/`` only."""
from __future__ import annotations

import ast
from pathlib import Path

WEBSERVICE_DIR = Path(__file__).resolve().parent.parent / "services" / "webservice"


# ------------------------------------------------------------------------------- the walk

def _iter_functions(tree):
    """Yield ``(name, node)`` for every function defined in *tree*, at any nesting depth."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def call_action_sites(tree) -> list[str]:
    """The functions of one module that call ``call_action`` (empty for a module that does not)."""
    found: list[str] = []
    for function, node in _iter_functions(tree):
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _called_name(child) == "call_action":
                found.append(function)
    return found


def declares_a_capability(tree, stem: str = "") -> bool:
    """Whether a module declares a capability — TWO shapes, both of them real registrations.

    1. ``register_pages(..., capabilities=<non-empty>)``: a module that registers its own routes
       (``pages/actions``, ``pages/mods``, ``pages/music``, the shell itself).
    2. a module-level ``capabilities()`` whose body RETURNS a non-empty dict, for a page whose routes
       join the ONE router ``shell._core_router`` registers — and ONLY when ``shell.py`` really merges
       that function (``capabilities.update(<stem>_page.capabilities())``). A page that defines the
       function without being merged declares nothing, and is still reported.

    The registrar's own ``register_pages`` definition and any other function of that name is exempted
    by callers, which scan a package that never defines it.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called_name(node) == "register_pages"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "capabilities":
                continue
            if isinstance(keyword.value, ast.Dict) and not keyword.value.keys:
                continue        # an explicitly EMPTY declaration is the hole, not a declaration
            return True
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == "capabilities"):
            continue
        if not _returns_a_capability_map(node):
            continue
        if stem and _shell_merges(stem):
            return True
    return False


def _returns_a_capability_map(function) -> bool:
    """Whether *function* RETURNS a non-empty ``dict`` literal (an empty one declares nothing)."""
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict) and node.value.keys:
            return True
    return False


def _shell_merges(stem: str) -> bool:
    """Whether ``shell._core_router`` merges *stem*'s ``capabilities()`` into the ONE registration.

    Read from the real module: the aggregated shape is a declaration only if the shell actually
    consumes it, so a page cannot satisfy this pin by defining a function nobody calls.
    """
    try:
        text = (WEBSERVICE_DIR / "shell.py").read_text(encoding="utf-8")
    except OSError:                                     # pragma: no cover - the shell always ships
        return False
    return f"capabilities.update({stem}_page.capabilities())" in text


def write_route_offenders(root: Path) -> list[str]:
    """Modules under *root* that call ``call_action`` WITHOUT declaring a capability.

    Empty is the property. Every entry names a write seam the access gate cannot see.
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as ex:               # pragma: no cover - a broken module is not a pass
            offenders.append(f"{path}: unparsable ({ex})")
            continue
        sites = call_action_sites(tree)
        if sites and not declares_a_capability(tree, path.stem):
            offenders.append(f"{path.relative_to(root)}: call_action in {', '.join(sorted(set(sites)))}")
    return offenders


def declaring_modules(root: Path) -> list[str]:
    """Modules under *root* that declare a capability (the scan's own anti-vacuity floor)."""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if declares_a_capability(tree, path.stem):
            out.append(str(path.relative_to(root)))
    return out


# ------------------------------------------------------------------------------- the pin

def test_every_call_action_site_in_the_webservice_declares_a_capability():
    """No module in the console's own package reaches an action undeclared.

    A route that calls ``call_action`` without ``capabilities=`` on its ``register_pages`` fails here,
    which is the whole point: the registrar cannot notice a MISSING declaration.
    """
    offenders = write_route_offenders(WEBSERVICE_DIR)
    assert offenders == [], (
        "a module under services/webservice/ calls call_action without declaring a capability; the "
        "access gate is then the only thing that could stop it and it declares nothing (deny by "
        f"default would refuse the route, or worse, the declaration is elsewhere): {offenders}")


def test_the_pin_scans_a_tree_that_does_declare_capabilities():
    """Anti-vacuity, both halves: the walk must FIND the package, and the declaration detector
    must recognise the declarations that ship today (``shell.py``'s ``capabilities=…``).

    A checker that reported nothing because it read nothing would pass for the wrong reason.
    """
    assert WEBSERVICE_DIR.is_dir(), "the pin is pointed at a directory that does not exist"
    modules = list(WEBSERVICE_DIR.rglob("*.py"))
    assert len(modules) >= 10, f"the walk found only {len(modules)} module(s)"
    assert declaring_modules(WEBSERVICE_DIR), (
        "no module in services/webservice/ declares a capability — either the tree changed shape "
        "or this pin stopped reading declarations, and both make the pin above meaningless")


def test_the_pin_reports_a_module_that_calls_call_action_without_a_capability(tmp_path):
    """The positive control: the DETECTOR fails on the tree this pin exists to refuse.

    Three modules, three verdicts — an undeclared caller is reported, a declared caller is not, and
    an explicitly EMPTY declaration is reported (it is the hole spelled out).
    """
    tree = tmp_path / "webservice"
    tree.mkdir()
    (tree / "lonely.py").write_text(
        "from core.actions import call_action\n"
        "async def handler(request):\n"
        "    return await call_action('pause_mission', None, server_name='SRS-1')\n",
        encoding="utf-8")
    (tree / "declared.py").write_text(
        "from core.actions import call_action\n"
        "def register(registrar, router):\n"
        "    registrar.register_pages('probe', router, capabilities={'/probe': 'public'})\n"
        "async def handler(request):\n"
        "    return await call_action('pause_mission', None, server_name='SRS-1')\n",
        encoding="utf-8")
    (tree / "empty.py").write_text(
        "from core.actions import call_action\n"
        "def register(registrar, router):\n"
        "    registrar.register_pages('probe', router, capabilities={})\n"
        "async def handler(request):\n"
        "    return await call_action('pause_mission', None, server_name='SRS-1')\n",
        encoding="utf-8")

    # a page that DEFINES a capabilities() map the shell never merges declares nothing either
    (tree / "unmerged.py").write_text(
        "from core.actions import call_action\n"
        "def capabilities():\n"
        "    return {'/unmerged': 'public'}\n"
        "async def handler(request):\n"
        "    return await call_action('pause_mission', None, server_name='SRS-1')\n",
        encoding="utf-8")

    offenders = write_route_offenders(tree)
    reported = {entry.split(":")[0] for entry in offenders}
    assert reported == {"lonely.py", "empty.py", "unmerged.py"}, (
        f"the detector must report exactly the undeclared callers, got {offenders}")
    assert any("handler" in entry for entry in offenders)
