"""The fresh-per-call YAML rule for ``plugins/``: no shared module-level ``YAML()``.

A ruamel ``YAML`` carries per-parse state (reader, scanner, parser, ``doc_infos``) and is not
thread-safe; two threads driving one instance corrupt each other's event stream. ``core/`` and
``services/`` were swept first; this closes the class over ``plugins/``, the last area. The tree
reaches a YAML parser both from the bot's event loop and from ``asyncio.to_thread`` offloads, so a
module-level ``yaml = YAML()`` is a latent cross-thread pair. ``core.utils.yaml_helper.new_yaml()``
returns a fresh instance per call at ~0.04 ms.

These sites are LATENT: most plugin singletons are reached only from Discord command handlers on the
one event loop, so no second thread was exercised against them. They are converted anyway to close
the class by construction, so a later ``to_thread`` offload cannot reopen it.

Four properties, each a test below:

1. **the guard** — a static AST scan asserts NO module-level ``YAML()`` binding remains anywhere
   under ``plugins/``. A binding inside a function/method is legal (per-call, dies with the frame)
   and is not reported; the one known function-local instance is named in ``FUNCTION_LOCAL``.
2. **the converted call sites** — every load/dump of the swept modules goes through ``new_yaml()``,
   and no module-global ``yaml.load(...)`` / ``yaml.dump(...)`` text is left in them.
3. **the guard can fail** — positive controls plant the exact pre-edit shape in a scratch module and
   assert the detector FLAGS it, and stays quiet on a function-local neighbour.
4. **output neutrality** — a dump through the OLD pattern (ONE module-level instance doing the load
   and the dump) and through ``new_yaml()`` is byte-identical for every YAML under ``samples/`` and
   for in-memory documents, so the sweep changes nothing that is written to operator files.
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

PLUGINS_DIR = REPO_ROOT / "plugins"

#: The OLD pattern exactly as these modules had it: ONE instance shared for every load and dump.
#: Kept here only to prove a dump through it is byte-identical to ``new_yaml()``.
_OLD_YAML = YAML()

#: Modules that keep a module-level ``YAML()`` ON PURPOSE, with the reason. An entry here is a claim
#: that no second thread can ever reach that instance. Empty under ``plugins/``: every module-level
#: binding was converted.
ALLOWED: dict[str, str] = {}

#: Modules that build a ``YAML()`` inside a FUNCTION or METHOD — per-call, dies with the frame, never
#: shared. These are left as they are (converting them would change settings, not just lifetime).
FUNCTION_LOCAL: dict[str, str] = {
    "plugins/mission/views.py":
        "line 389: ``YAML()`` local to ``MenuView``/dump helper, then sets ``indent`` / "
        "``default_flow_style`` / ``sort_keys`` — ``new_yaml()`` defaults would change the bytes",
}

#: Modules converted by this sweep, with the number of ``new_yaml()`` load / dump calls each carries.
#: The counts are the census the conversion was driven by; a NEW call site in one of these files is
#: expected to update the entry, which is the point — the numbers cannot drift silently.
CONVERTED: dict[str, tuple[int, int]] = {
    "plugins/greenieboard/commands.py": (1, 1),
    "plugins/scheduler/actions.py": (1, 1),
    "plugins/firewall/commands.py": (2, 2),
    "plugins/scheduler/commands.py": (1, 2),
    "plugins/scheduler/views.py": (1, 1),
    "plugins/slotblocking/commands.py": (2, 2),
    "plugins/funkman/commands.py": (0, 1),
    "plugins/userstats/commands.py": (0, 1),
    "plugins/mission/menu.py": (1, 0),
    "plugins/mission/migrate.py": (7, 7),
    "plugins/monitoring/commands.py": (3, 2),
    "plugins/commands/commands.py": (2, 2),
    "plugins/mission/commands.py": (3, 2),
    "plugins/backup/commands.py": (1, 0),
    "plugins/dks/commands.py": (1, 1),
    "plugins/restapi/commands.py": (1, 1),
}

#: Modules that bound a module-level ``yaml = YAML()`` but never called it: the binding AND its
#: ruamel import were deleted, and no ``new_yaml`` import was added (there is nothing to parse).
DEAD_BINDINGS: list[str] = [
    "plugins/admin/commands.py",
    "plugins/tournament/commands.py",
    "plugins/voting/listener.py",
]


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
                scan(stmt.body)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
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


def _scanned_files() -> list[Path]:
    """Every ``*.py`` under ``plugins/`` (``__pycache__`` excluded)."""
    return sorted(p for p in PLUGINS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _offenders() -> list[str]:
    bad: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for lineno, name in _module_level_yaml_bindings(path.read_text(encoding="utf-8")):
            bad.append(f"{rel}:{lineno}: {name} = YAML()")
    return bad


# --------------------------------------------------------------- 1. the guard

def test_no_module_level_yaml_binding_under_plugins():
    """No module-level ``YAML()`` binding remains under ``plugins/``. A failure names every
    offending file, line and binding."""
    assert PLUGINS_DIR.is_dir(), "expected plugins/ at the repo root"
    scanned = _scanned_files()
    assert len(scanned) > 50, f"the scan must actually walk the tree, saw only {len(scanned)} files"

    offenders = _offenders()
    assert not offenders, (
        "module-level YAML() bindings are shared across threads; use "
        "core.utils.yaml_helper.new_yaml() per call instead:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_reaches_every_converted_source():
    """The guard is only meaningful if it walks the whole tree: every converted module must be in
    the scanned set, by path, and no longer reported."""
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in _scanned_files()}
    for rel in list(CONVERTED) + DEAD_BINDINGS + list(FUNCTION_LOCAL):
        assert rel in scanned, f"{rel} is under plugins/ and must be scanned"
        assert rel not in ALLOWED
        assert _module_level_yaml_bindings(
            (REPO_ROOT / rel).read_text(encoding="utf-8")) == [], f"{rel} still binds a module YAML()"


def test_the_function_local_instance_is_left_alone():
    """``plugins/mission/views.py`` builds a ``YAML()`` inside a function and then sets ``indent`` /
    ``default_flow_style`` / ``sort_keys``. It is per-call and its settings are not ``new_yaml()``'s,
    so it stays; the allow-list names it and it must still be there."""
    assert list(FUNCTION_LOCAL) == ["plugins/mission/views.py"]
    assert all(reason.strip() for reason in FUNCTION_LOCAL.values())

    source = (REPO_ROOT / "plugins/mission/views.py").read_text(encoding="utf-8")
    assert "from ruamel.yaml import YAML" in source
    assert "    yaml = YAML()" in source, "the function-local construction must still be present"
    assert _module_level_yaml_bindings(source) == [], "it must stay function-local"


# --------------------------------------------------------------- 2. the converted call sites

def test_every_load_and_dump_of_the_swept_modules_goes_through_new_yaml():
    """Each converted module carries at least the census of ``new_yaml()`` calls it was converted to,
    and keeps no ``yaml.load(`` / ``yaml.dump(`` text (which would be a module global)."""
    for rel, (loads, dumps) in CONVERTED.items():
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert source.count("new_yaml().load(") >= loads, f"{rel}: expected >= {loads} new_yaml loads"
        assert source.count("new_yaml().dump(") >= dumps, f"{rel}: expected >= {dumps} new_yaml dumps"
        assert "yaml.load(" not in source, f"{rel}: a module-global yaml.load(...) is left"
        assert "yaml.dump(" not in source, f"{rel}: a module-global yaml.dump(...) is left"

    assert sum(loads for loads, _ in CONVERTED.values()) == 27
    assert sum(dumps for _, dumps in CONVERTED.values()) == 26


def test_the_swept_modules_import_new_yaml_and_not_a_shared_instance():
    """The modules that still need a parser import the factory; the three that stopped using one at
    all carry no ruamel import."""
    for rel in CONVERTED:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "from core.utils.yaml_helper import new_yaml" in source, f"{rel} must import new_yaml"
        assert "from ruamel.yaml import YAML" not in source, f"{rel} must not import YAML anymore"

    for rel in DEAD_BINDINGS:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "ruamel" not in source, f"{rel}: the binding was unused; the import goes with it"
        assert "new_yaml" not in source, f"{rel}: no parser is needed, no factory import"


# --------------------------------------------------------------- 3. the positive controls

def test_the_detector_flags_the_exact_pre_edit_shape():
    """POSITIVE CONTROL: the exact lines the sweep deleted are flagged."""
    planted = (
        "from core.data.node import Node\n"
        "\n"
        "# ruamel YAML support\n"
        "from ruamel.yaml import YAML\n"
        "from ruamel.yaml.error import MarkedYAMLError\n"
        "yaml = YAML()\n"
    )
    assert _module_level_yaml_bindings(planted) == [(6, "yaml")]


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
    """A class attribute is ONE object shared by every instance and caller: flagged."""
    planted = (
        "from ruamel.yaml import YAML\n"
        "class Loader:\n"
        "    yaml = YAML()\n"
    )
    assert _module_level_yaml_bindings(planted) == [(3, "yaml")]


def test_the_detector_stays_quiet_on_legal_neighbours():
    """A ``YAML()`` bound inside a function or method is per-call and must NOT be reported. The
    first shape is live in the tree: ``plugins/mission/views.py`` dumped through a function-local
    instance with style overrides."""
    function_local = (
        "from ruamel.yaml import YAML\n"
        "def load(text):\n"
        "    yaml = YAML()\n"
        "    return yaml.load(text)\n"
    )
    assert _module_level_yaml_bindings(function_local) == []

    method_local = (
        "from ruamel.yaml import YAML\n"
        "class Wrapper:\n"
        "    def dump(self, data, stream):\n"
        "        yaml = YAML()\n"
        "        yaml.indent(mapping=2, sequence=4, offset=2)\n"
        "        yaml.dump(data, stream)\n"
    )
    assert _module_level_yaml_bindings(method_local) == []

    inline = "from ruamel.yaml import YAML\ndata = YAML().load('a: 1')\n"
    assert _module_level_yaml_bindings(inline) == []


def test_the_detector_stays_quiet_on_the_new_pattern():
    """The replacement itself must not trip the guard."""
    new_pattern = (
        "from core.utils.yaml_helper import new_yaml\n"
        "data = new_yaml().load('a: 1')\n"
    )
    assert _module_level_yaml_bindings(new_pattern) == []


def test_the_detector_flags_a_planted_binding_in_a_scratch_module(tmp_path):
    """The scratch-module control: a real file on disk with the exact pre-edit binding is flagged,
    and a function-local neighbour in the same file is not — so the detector is not merely quiet."""
    scratch = tmp_path / "scratch_plugin.py"
    scratch.write_text(
        "from ruamel.yaml import YAML\n"
        "\n"
        "# ruamel YAML support\n"
        "yaml = YAML()\n"
        "\n"
        "\n"
        "def rebuild(text):\n"
        "    local = YAML()\n"
        "    return local.load(text)\n",
        encoding="utf-8",
    )
    assert _module_level_yaml_bindings(scratch.read_text(encoding="utf-8")) == [(4, "yaml")]


# --------------------------------------------------------------- 4. output neutrality

#: The plugin writers this sweep converts, and the operator files they touch (line numbers are the
#: converted dump sites):
#:
#:   plugins/mission/commands.py:887                      presets.yaml (preset add)
#:   plugins/mission/commands.py:1702                     servers.yaml (afk-exemption edit)
#:   plugins/mission/migrate.py:49,75,174,181,198,208,216 userstats.yaml / mission.yaml /
#:       servers.yaml / main.yaml / nodes.yaml migration writers
#:   plugins/monitoring/commands.py:118,161               main.yaml and services/monitoring.yaml
#:   plugins/scheduler/actions.py:453                     servers.yaml (F3 override sync)
#:   plugins/scheduler/views.py:470                       servers.yaml (server-settings edit)
#:   plugins/scheduler/commands.py:52,121                 plugins/scheduler.yaml
#:   plugins/greenieboard/commands.py:161                 plugins/greenieboard.yaml
#:   plugins/slotblocking/commands.py:61,66               plugins/slotblocking.yaml and servers.yaml
#:   plugins/funkman/commands.py:65                       plugins/funkman.yaml
#:   plugins/userstats/commands.py:100                    plugins/userstats.yaml
#:   plugins/commands/commands.py:116,124                 plugins/commands.yaml and services/bot.yaml
#:   plugins/dks/commands.py:154                          plugins/restapi.yaml
#:   plugins/restapi/commands.py:131                      plugins/restapi.yaml
#:   plugins/firewall/commands.py:185,220                 services/firewall.yaml
#:   plugins/mission/menu.py                             menus.yaml (load only, no dump)
SAMPLE_FILES = sorted(p for p in REPO_ROOT.glob("samples/**/*.yaml") if p.is_file())


def _dump_with(yaml, data) -> str:
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def _old_pattern(text: str) -> str:
    """The OLD pattern: the module-level instance loads AND dumps."""
    return _dump_with(_OLD_YAML, _OLD_YAML.load(text))


def _new_pattern(text: str) -> str:
    """The converted pattern: one fresh instance to load, another to dump."""
    return _dump_with(new_yaml(), new_yaml().load(text))


def test_the_proof_covers_every_sample_config():
    """The proof is only worth as much as its inputs: every YAML under ``samples/`` is used."""
    assert len(SAMPLE_FILES) >= 30, f"expected the sample config tree, saw {len(SAMPLE_FILES)} files"
    names = {p.name for p in SAMPLE_FILES}
    for expected in ("main.yaml", "nodes.yaml", "servers.yaml", "presets.yaml", "menus.yaml"):
        assert expected in names


def test_a_round_trip_dump_is_byte_identical_for_every_sample_config():
    """The operator-facing writers: load a real sample, dump it, and the two patterns must emit the
    SAME BYTES. This stands for the converted dump sites named above."""
    checked = 0
    for path in SAMPLE_FILES:
        text = path.read_text(encoding="utf-8")
        old = _old_pattern(text)
        new = _new_pattern(text)
        assert new == old, f"{path.relative_to(REPO_ROOT)}: dump differs between the two patterns"
        assert new.encode("utf-8") == old.encode("utf-8")
        checked += 1
    assert checked == len(SAMPLE_FILES)


def test_a_preset_document_dumps_byte_identically():
    """``plugins/mission/commands.py`` writes ``presets.yaml`` from a document it built (weather
    fields, a nested dict, a long line): both patterns must emit the same bytes."""
    data = {
        "DEFAULT": {
            "start_time": 64800, "date": "2024-06-01", "temperature": 16, "clouds": {"density": 0.3},
            "wind": {"speed": 4, "direction": 270}, "groundTurbulence": 0.5, "enable_fog": False,
            "fog": {"thickness": 0, "visibility": 0}, "halo": "none",
        },
        "long": "z" * 300,
        "seq": [{"inner": True}, "alpha", 3],
        "empty": {},
        "quoted": "needs 'quotes'",
    }
    old = _dump_with(_OLD_YAML, data)
    new = _dump_with(new_yaml(), data)
    assert new == old
    assert new.encode("utf-8") == old.encode("utf-8")


def test_a_menu_document_dumps_byte_identically():
    """A ``menus.yaml`` shape is a list of command items; the round-trip must keep it identical."""
    data = [
        {"command": "mission", "label": "Mission", "params": {"name": "add_preset"}, "conditions": []},
        {"command": "userstats", "label": "Stats", "items": [{"command": "linkme"}]},
    ]
    old = _dump_with(_OLD_YAML, data)
    new = _dump_with(new_yaml(), data)
    assert new == old
    assert new.encode("utf-8") == old.encode("utf-8")


def test_a_comment_bearing_servers_yaml_is_unchanged_by_the_rewrite():
    """``servers.yaml`` is hand-edited: comments and blank lines must survive, and both patterns
    must keep them identically (the migration and override-sync writers rely on this)."""
    text = (REPO_ROOT / "samples" / "servers.yaml").read_text(encoding="utf-8")
    assert "#" in text, "the fixture must carry comments for this proof to mean anything"
    old = _old_pattern(text)
    new = _new_pattern(text)
    assert new == old
    assert new.count("#") == old.count("#") and new.count("#") > 0


def test_new_yaml_returns_a_fresh_instance_every_call():
    """The whole point: two calls never hand back the same object."""
    first = new_yaml()
    second = new_yaml()
    assert first is not second
    assert "rt" in first.typ and "rt" in second.typ
