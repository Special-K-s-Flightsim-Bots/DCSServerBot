"""The fresh-per-call YAML rule for ``core/`` and ``services/``: no shared module-level ``YAML()``.

A ruamel ``YAML`` carries per-parse state (reader, scanner, parser, ``doc_infos``) and is not
thread-safe; two threads driving one instance corrupt each other's event stream. The tree reaches a
YAML parser both from the bot's event loop and from ``asyncio.to_thread`` offloads (web requests,
install steps, file watchers), so a module-level ``yaml = YAML()`` is a live cross-thread pair. The
class is closed by construction: ``core.utils.yaml_helper.new_yaml()`` returns a fresh instance per
call at ~0.04 ms.

Four properties, each a test below:

1. **the guard** — a static AST scan asserts NO module-level ``YAML()`` binding remains anywhere
   under ``core/`` or ``services/`` except the single allow-listed module (see ``ALLOWED``).
   A binding inside a function/method is legal (per-call, dies with the frame) and is not reported.
2. **the converted call sites** — every load/dump of the swept modules goes through ``new_yaml()``,
   and no module-global ``yaml.load(...)`` / ``yaml.dump(...)`` text is left in them.
3. **the guard can fail** — positive controls plant the exact pre-edit shape in a scratch string and
   assert the detector FLAGS it, and stays quiet on legal neighbours.
4. **output neutrality** — a dump through the OLD pattern (ONE module-level instance doing the load
   and the dump) and through ``new_yaml()`` is byte-identical for every YAML under ``samples/`` and
   for in-memory documents, so the sweep changes nothing that is written to disk.
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

CORE_DIR = REPO_ROOT / "core"
SERVICES_DIR = REPO_ROOT / "services"

#: The OLD pattern exactly as these modules had it: ONE instance shared for every load and dump.
#: Kept here only to prove a dump through it is byte-identical to ``new_yaml()``.
_OLD_YAML = YAML()

#: Modules that keep a module-level ``YAML()`` ON PURPOSE, with the reason. An entry here is a claim
#: that no second thread can ever reach that instance.
ALLOWED: dict[str, str] = {
    "core/utils/validators.py":
        "its module-level ``yaml`` is read ONLY from ``NodeData._initialize`` (lines 89 and 105), "
        "which runs under ``NodeData._lock`` in ``NodeData.__new__``'s double-checked locking — the "
        "singleton is built once, serialised by construction",
}

#: Modules converted by this sweep, with the number of ``new_yaml()`` load / dump calls each carries.
#: The counts are the census the conversion was driven by; a NEW call site in one of these files is
#: expected to update the entry, which is the point — the numbers cannot drift silently.
CONVERTED: dict[str, tuple[int, int]] = {
    "core/data/impl/nodeimpl.py": (7, 4),
    "core/data/impl/serverimpl.py": (6, 4),
    "core/data/node.py": (2, 1),
    "core/data/proxy/nodeproxy.py": (1, 0),
    "core/data/server.py": (1, 0),
    "core/plugin.py": (2, 1),
    "core/services/base.py": (1, 1),
    "core/translations.py": (1, 0),
    "core/utils/helper.py": (3, 1),
    "services/bot/dummy/guild.py": (2, 0),
    "services/bot/service.py": (0, 0),
    "services/music/radios/base.py": (0, 0),
    "services/webservice/service.py": (0, 1),
}

#: The modules that stopped using a parser ALTOGETHER: their load and dump went to a primitive that
#: owns its own (``core.utils.configwrite``), so importing the factory would be a dead import.
NO_PARSER: tuple[str, ...] = ("services/bot/service.py", "services/music/radios/base.py")

#: ``services/webservice/auth/local.py`` needs a typed parser, so it builds its own per call instead
#: of going through ``new_yaml()`` (which is ``typ='rt'``): the same rule — one instance per call.
SAFE_PARSER_FILE = "services/webservice/auth/local.py"


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


def _scanned_files() -> list[Path]:
    """Every ``*.py`` under ``core/`` and ``services/`` (``__pycache__`` excluded)."""
    out: list[Path] = []
    for base in (CORE_DIR, SERVICES_DIR):
        out.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(out)


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

def test_no_module_level_yaml_binding_under_core_and_services():
    """No module-level ``YAML()`` binding remains under ``core/`` or ``services/`` outside the
    allow-list. A failure names every offending file, line and binding."""
    assert CORE_DIR.is_dir() and SERVICES_DIR.is_dir(), "expected core/ and services/ at the repo root"
    scanned = _scanned_files()
    assert len(scanned) > 50, f"the scan must actually walk the tree, saw only {len(scanned)} files"

    offenders = _offenders()
    assert not offenders, (
        "module-level YAML() bindings are shared across threads; use "
        "core.utils.yaml_helper.new_yaml() per call instead:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_is_the_only_exemption():
    """The allow-list is one module with a stated reason, and that module still needs it — otherwise
    the exemption would be stale and should be deleted."""
    assert list(ALLOWED) == ["core/utils/validators.py"]
    assert all(reason.strip() for reason in ALLOWED.values())

    exempt = REPO_ROOT / "core/utils/validators.py"
    bindings = _module_level_yaml_bindings(exempt.read_text(encoding="utf-8"))
    assert bindings, "the allow-list entry no longer carries a module-level YAML() - remove it"
    assert [name for _, name in bindings] == ["yaml"]


def test_the_scan_reaches_every_converted_source():
    """The guard is only meaningful if it walks the whole tree: every converted module must be in
    the scanned set, by path, and no longer reported."""
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in _scanned_files()}
    for rel in list(CONVERTED) + [SAFE_PARSER_FILE]:
        assert rel in scanned, f"{rel} is under core/ or services/ and must be scanned"
        assert rel not in ALLOWED
        assert _module_level_yaml_bindings(
            (REPO_ROOT / rel).read_text(encoding="utf-8")) == [], f"{rel} still binds a module YAML()"


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

    assert sum(loads for loads, _ in CONVERTED.values()) == 26
    assert sum(dumps for _, dumps in CONVERTED.values()) == 13


def test_the_swept_modules_import_new_yaml_and_not_a_shared_instance():
    """The modules that still need a parser import the factory; the two that stopped using one at
    all carry no ruamel import."""
    for rel in CONVERTED:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if rel in NO_PARSER:
            assert "ruamel" not in source, "the binding was unused; the import goes with it"
            assert "from core.utils.yaml_helper import new_yaml" not in source, rel
            continue
        assert "from core.utils.yaml_helper import new_yaml" in source, f"{rel} must import new_yaml"
        assert "from ruamel.yaml import YAML" not in source, f"{rel} must not import YAML anymore"


def test_the_safe_parser_site_builds_its_own_instance_per_call():
    """``services/webservice/auth/local.py`` reads ``bot.yaml`` with ``typ='safe'`` on purpose.
    ``new_yaml()`` is ``typ='rt'``, so routing it there would change the PARSER, not just the
    lifetime; it builds a safe instance per call instead — the same rule."""
    rel = SAFE_PARSER_FILE
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert 'YAML(typ="safe").load(' in source
    assert 'yaml = YAML(typ="safe")' not in source
    assert source.count("YAML(") == 1, "one per-call construction, no module-level binding"
    assert "from ruamel.yaml import YAML" in source


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
    """A ``YAML()`` bound inside a function, a method or an annotation-less branch of one is
    per-call and must NOT be reported. The two shapes below are live in the tree:
    ``core/plugin_manager.py::_AgentPluginWrapper._read_locals`` and
    ``core/utils/configwrite.py::yaml_roundtrip``."""
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
        "    def _read_locals(self):\n"
        "        from ruamel.yaml import YAML\n"
        "        yaml = YAML()\n"
        "        return yaml.load('a: 1')\n"
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


# --------------------------------------------------------------- 4. output neutrality

#: The files whose data shape this sweep dumps, and the sites the proof stands for (line numbers are
#: the converted ones):
#:
#:   core/data/impl/nodeimpl.py:469,2416,2423,2522,2544                      nodes.yaml /
#:       main.yaml / config/plugins/*.yaml / config/services/*.yaml writers. Its instance
#:       writers (add/delete/rename instance) and its two whole-document nodes.yaml writers
#:       (set_config, init_instances) no longer dump: they write their own channel of
#:       nodes.yaml through core/utils/configwrite.patch_config_file, which carries the same
#:       per-call-instance rule.
#:   core/data/impl/serverimpl.py:401,640,1297,1682                                 servers.yaml
#:       (channels, rename, settings) and nodes.yaml (extension config)
#:   core/data/node.py:174                                                           main.yaml writer
#:   core/plugin.py:431                                                              config/plugins/<name>.yaml
#:   core/services/base.py:196                                                       config/services/<name>.yaml
#:   core/utils/helper.py:907                                                        SettingsDict.save -> its own path
#:       (presets*.yaml / servers.yaml)
#:   services/music/radios/base.py:108                                               config/services/music.yaml
#:   services/webservice/service.py:82                                               config/services/webservice.yaml
#:       (the config it creates for a REST API installation; it never writes that plugin's own file)
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
    SAME BYTES."""
    checked = 0
    for path in SAMPLE_FILES:
        text = path.read_text(encoding="utf-8")
        old = _old_pattern(text)
        new = _new_pattern(text)
        assert new == old, f"{path.relative_to(REPO_ROOT)}: dump differs between the two patterns"
        assert new.encode("utf-8") == old.encode("utf-8")
        checked += 1
    assert checked == len(SAMPLE_FILES)


def test_an_in_memory_document_dumps_byte_identically():
    """Sites that dump a document they built (``Service.save_config`` and the webservice's
    auto-created config) never loaded anything: a plain dict, a long line and a nested sequence."""
    data = {
        "DEFAULT": {"channels": {"status": 1, "chat": 2, "admin": -1}},
        "DCS1": {"serverSettings": {"name": "DCS1", "maxPlayers": 16}, "instances": {"DCS1": {"port": 6666}}},
        "long": "z" * 300,
        "seq": [{"inner": True}, "alpha", 3],
        "empty": {},
        "quoted": "needs 'quotes'",
    }
    old = _dump_with(_OLD_YAML, data)
    new = _dump_with(new_yaml(), data)
    assert new == old
    assert new.encode("utf-8") == old.encode("utf-8")


def test_a_comment_bearing_file_is_unchanged_by_the_rewrite():
    """The files these writers touch are hand-edited: comments and blank lines must survive, and both
    patterns must keep them identically."""
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
