"""The SETTINGS CATALOG — the items the Settings LANDING page shows as tiles.

WHY THIS EXISTS
---------------
The Settings area used to open on a list of YAML filenames: an operator could not tell a plugin from
a service from an extension, and "what is installed" had no shape. The redesign makes the landing
page a grid of TILES — one per plugin, one per service, one per extension — and this module is the
ONE place the service and extension lists are read, the way :mod:`plugin_registry` already reads the
plugin list.

IT READS THE TREE, NEVER A TABLE
--------------------------------
Nothing here is a hand-kept list:

* a **service** is a package under ``services/`` carrying a ``service.py`` (the module the bot's own
  loader imports — see ``core/services/registry.py::ServiceRegistry.run`` +
  ``core/utils/helper.py::dynamic_import``). Its one-line description is the first prose line of its
  OWN ``README.md``; its plugin dependency and its placement (``master_only`` / ``agent_only``) come
  from its own ``@ServiceRegistry.register(...)`` decorator, parsed with :mod:`ast` — never imported,
  because importing a service pulls ``discord``/``psycopg`` and the bot itself.
* an **extension** is a package under ``extensions/``. Its CONFIG SCOPE is read from the two schema
  blocks the bot validates extensions with (``extensions/schemas/extensions_schema.yaml``:
  ``node_extensions_schema`` / ``instance_extensions_schema``): an extension named in both is
  configurable per node AND per instance, named in one is scoped to that one, named in neither has no
  schema config at all. Its description is the first prose line of its OWN ``README.md``.

THE SAME README-FIRST-LINE MECHANISM FOR BOTH KINDS — the plugin registry's is reused verbatim in
spirit and re-stated here once (``plugin_registry`` keeps its own copy for its own block; the two are
short and independent, and neither imports the other so a change to one cannot surprise the other).

NO WRITE PATH HERE. This module only READS the tree and builds a render model. A service's on/off is
a FILE action (see :mod:`services.webservice.pages.settings`), and a plugin's on/off is the
``opt_plugins`` list the existing plugin block already writes — this module writes nothing.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from . import i18n

__all__ = [
    "REPO_ROOT", "SERVICES_DIR", "EXTENSIONS_DIR", "SERVICE_FILE", "README_FILE",
    "EXTENSIONS_SCHEMA", "PLUGIN_DEP_DISABLED_LABEL",
    "SCOPE_NODE", "SCOPE_INSTANCE", "SCOPE_BOTH", "SCOPE_NONE",
    "SCOPE_LABELS", "PLACEMENT_LABELS", "PLACEMENT_MASTER", "PLACEMENT_AGENT", "PLACEMENT_BOTH",
    "KIND_PLUGIN", "KIND_SERVICE", "KIND_EXTENSION", "kind_labels",
    "ServiceInfo", "ExtensionInfo", "extension_scopes",
    "discover_services", "discover_extensions", "service_config_path", "disabled_config_path",
    "default_extensions",
]

#: repo root, addressed absolutely from THIS file so the catalog never depends on the process's
#: working directory (``services/webservice/catalog.py`` -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "services"
EXTENSIONS_DIR = REPO_ROOT / "extensions"
#: the module the bot's loader imports for every service (``services.<name>.service``); a folder
#: WITHOUT it is not a loadable service.
SERVICE_FILE = "service.py"
README_FILE = "README.md"
#: the schema file the bot validates extensions with — the source of an extension's CONFIG SCOPE.
EXTENSIONS_SCHEMA = EXTENSIONS_DIR / "schemas" / "extensions_schema.yaml"

#: an extension's config scopes — a FACT read from the schema, not a decoration.
SCOPE_NODE = "node"
SCOPE_INSTANCE = "instance"
SCOPE_BOTH = "both"
SCOPE_NONE = "none"

#: the scope words the tile prints, born wrapped (``i18n._``) like every other composed word.
SCOPE_LABELS: dict[str, str] = {
    SCOPE_NODE: i18n._("per node"),
    SCOPE_INSTANCE: i18n._("per instance"),
    SCOPE_BOTH: i18n._("node + instance"),
    SCOPE_NONE: i18n._("always on"),
}

#: where a service runs — the registry's own placement declarations (``master_only`` / ``agent_only``).
PLACEMENT_MASTER = "master"
PLACEMENT_AGENT = "agent"
PLACEMENT_BOTH = "both"
PLACEMENT_LABELS: dict[str, str] = {
    PLACEMENT_MASTER: i18n._("master node"),
    PLACEMENT_AGENT: i18n._("agent node"),
    PLACEMENT_BOTH: i18n._("both nodes"),
}

#: the kind words a tile carries (the kind is ALSO carried by the rail, the glyph and the scope line).
KIND_PLUGIN = "plugin"
KIND_SERVICE = "service"
KIND_EXTENSION = "ext"
_kind_words = {
    KIND_PLUGIN: i18n._("Plugin"),
    KIND_SERVICE: i18n._("Service"),
    KIND_EXTENSION: i18n._("Extension"),
}


def kind_labels() -> dict[str, str]:
    """The kind word per kind — read through a function so the module import stays cheap for tests."""
    return dict(_kind_words)


#: The sentence a service tile shows when its plugin dependency holds it off (the file EXISTS, so the
#: fix is the plugin list, not a file move). A ``%(plugin)s`` hole names the plugin.
PLUGIN_DEP_DISABLED_LABEL = i18n._("held off by the %(plugin)s plugin: add it to opt_plugins")


@dataclass(frozen=True)
class ServiceInfo:
    """One service that EXISTS on disk: its name, its own one-line description, its declared plugin
    dependency (or ``None``), where the registry runs it, and whether its config file is present."""

    name: str
    description: str
    plugin: str | None
    placement: str
    configured: bool

    @property
    def plugin_dependency_label(self) -> str:
        return PLUGIN_DEP_DISABLED_LABEL if self.plugin else ""


@dataclass(frozen=True)
class ExtensionInfo:
    """One extension that EXISTS on disk: its name (the schema's own key), its one-line description
    and the CONFIG SCOPE the schema gives it."""

    name: str
    description: str
    scope: str


# ------------------------------------------------------------------ the README-first-line mechanism

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    """One readable line from a README's prose: links flattened, tags and emphasis dropped."""
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("`", "").replace("*", "").replace("_", " ")
    return _WS.sub(" ", text).strip()


def _readme_first_line(component_dir: Path) -> str:
    """The first PROSE line of a component's ``README.md`` — its opening sentence.

    Heading lines (``#``), blockquotes/notes (``>``), table rows (``|``) and rules (``---``/``***``)
    are skipped, so what is returned is the sentence a reader sees as the component's summary.
    """
    try:
        lines = (component_dir / README_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "#>|*" or stripped.startswith("---"):
            continue
        cleaned = _clean(stripped)
        if cleaned:
            return cleaned
    return ""


# ------------------------------------------------------------------------------- services


def _service_meta(service_py: Path) -> tuple[str, str, str | None, str] | None:
    """Read a service module's ``(class_name, init_name, plugin, placement)`` from its own source.

    Parsed with :mod:`ast`, never imported: rendering the page must not import every service (they
    pull ``discord``, ``psycopg`` and the bot itself). ``init_name`` is the ``name=`` a service hands
    ``super().__init__`` (its registered name — ``Backup``, ``Music``), or the class name when it
    passes none (``ServiceBus``, ``WebService``). Returns ``None`` for a module with no decorated
    ``Service`` class — not a loadable service.
    """
    try:
        tree = ast.parse(service_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and func.attr == "register"):
                continue
            plugin = None
            master_only = agent_only = False
            for keyword in decorator.keywords:
                if keyword.arg == "plugin" and isinstance(keyword.value, ast.Constant):
                    plugin = str(keyword.value.value).lower()
                elif keyword.arg == "master_only":
                    master_only = _const_true(keyword.value)
                elif keyword.arg == "agent_only":
                    agent_only = _const_true(keyword.value)
            placement = (PLACEMENT_MASTER if master_only else
                         (PLACEMENT_AGENT if agent_only else PLACEMENT_BOTH))
            return node.name, _init_name(node) or node.name, plugin, placement
    return None


def _init_name(class_node: ast.ClassDef) -> str | None:
    """The ``name=`` a class hands ``super().__init__`` (its registered name), or ``None``."""
    for child in ast.walk(class_node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr == "__init__":
                for keyword in child.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        return str(keyword.value.value)
    return None


def _const_true(value: ast.AST) -> bool:
    """Whether an AST node is the literal ``True`` (a non-literal is treated as not-set, honestly)."""
    return isinstance(value, ast.Constant) and value.value is True


def service_config_path(config_dir: Path, name: str) -> Path:
    """A service's config file — the SAME path ``Service.read_locals`` reads (``<name>.lower()``)."""
    return Path(config_dir) / "services" / f"{name.lower()}.yaml"


def disabled_config_path(config_dir: Path, name: str) -> Path:
    """Where a service's config is parked when it is turned OFF — beside its real path (``.disabled``)."""
    return Path(config_dir) / "services" / f"{name.lower()}.yaml.disabled"


def discover_services(config_dir: Path, services_dir: Path = SERVICES_DIR) -> list[ServiceInfo]:
    """Every service that EXISTS on disk, in name order — a reading of the tree, never a list.

    A service is a DIRECTORY under *services_dir* carrying a ``service.py`` (the module the bot
    loads). ``__pycache__`` and dotted folders are skipped by that test naturally. ``configured`` is
    whether the service's own ``config/services/<name>.yaml`` is present — the bot's de-facto
    enabled state (a missing file means the service no-ops).
    """
    services_root = Path(services_dir)
    if not services_root.is_dir():
        return []
    found: list[ServiceInfo] = []
    for child in sorted(services_root.iterdir()):
        if not child.is_dir() or child.name.startswith("__") or child.name.startswith("."):
            continue
        meta = _service_meta(child / SERVICE_FILE)
        if meta is None:
            continue
        class_name, init_name, plugin, placement = meta
        name = init_name or class_name
        found.append(ServiceInfo(
            name=name,
            description=_readme_first_line(child),
            plugin=plugin,
            placement=placement,
            configured=service_config_path(config_dir, name).is_file(),
        ))
    return found


# ----------------------------------------------------------------------------- extensions


def extension_scopes(schema_path: Path = EXTENSIONS_SCHEMA) -> dict[str, str]:
    """Extension name -> its CONFIG SCOPE, read from the two schema blocks the bot validates with.

    The keys of ``node_extensions_schema`` are the extensions configurable per NODE; the keys of
    ``instance_extensions_schema`` are those configurable per INSTANCE. An extension in both is
    :data:`SCOPE_BOTH`. A schema that cannot be read yields no scopes rather than raising.
    """
    try:
        data = YAML(typ="safe").load(Path(schema_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/broken schema is "no scope knowledge", never fatal
        return {}
    if not isinstance(data, dict):
        return {}
    node = _mapping_keys(data.get("schema;node_extensions_schema"))
    instance = _mapping_keys(data.get("schema;instance_extensions_schema"))
    scopes: dict[str, str] = {}
    for key in node | instance:
        in_node, in_instance = key in node, key in instance
        scopes[key] = (SCOPE_BOTH if in_node and in_instance else
                       SCOPE_NODE if in_node else SCOPE_INSTANCE)
    return scopes


def _mapping_keys(block) -> set[str]:
    """The mapping keys of one pykwalify schema block, or the empty set."""
    if isinstance(block, dict) and isinstance(block.get("mapping"), dict):
        return {str(key) for key in block["mapping"]}
    return set()


def _extension_dir_name(name: str) -> str:
    """The folder a schema key lives in: the key lower-cased (``RealWeather`` -> ``realweather``)."""
    return name.lower()


def discover_extensions(extensions_dir: Path = EXTENSIONS_DIR,
                        schema_path: Path = EXTENSIONS_SCHEMA) -> list[ExtensionInfo]:
    """Every extension that EXISTS on disk, in name order — a reading of the tree.

    An extension is a DIRECTORY under *extensions_dir* (skipping ``__pycache__``, dotted folders and
    the ``schemas`` folder the schemas themselves live in). Its display name is the SCHEMA's own key
    when the folder maps to one (so the tile says ``RealWeather``, not ``realweather``); otherwise
    the folder name is title-cased. Its scope is :func:`extension_scopes`' answer, defaulting to
    :data:`SCOPE_NONE` (no schema config) for an extension the schema does not name.
    """
    extensions_root = Path(extensions_dir)
    if not extensions_root.is_dir():
        return []
    scopes = extension_scopes(schema_path)
    by_lower = {key.lower(): key for key in scopes}
    found: list[ExtensionInfo] = []
    for child in sorted(extensions_root.iterdir()):
        if not child.is_dir() or child.name.startswith("__") or child.name.startswith("."):
            continue
        if child.name == "schemas":
            continue
        canonical = by_lower.get(child.name.lower(), child.name)
        found.append(ExtensionInfo(
            name=canonical,
            description=_readme_first_line(child),
            scope=scopes.get(canonical, SCOPE_NONE),
        ))
    return found


#: The bot's DEFAULT extensions — the names the runtime loads even when ``nodes.yaml`` says NOTHING
#: about them, and which an ``enabled: false`` in a block turns off. Read from the source of truth so
#: the marker is never a hand-kept list.
_DEFAULT_EXTENSIONS: dict[str, str] | None = None


def default_extensions() -> dict[str, str]:
    """The bot's OWN default extension set, as ``{lower-cased name: the name as it is spelled}``.

    ``DEFAULT_EXTENSIONS`` lives in ``core/data/impl/serverimpl.py`` and is merged UNDER the file's own
    blocks by ``load_extension``/``init_extensions`` — so those names RUN even when ``nodes.yaml``
    configures nothing, and a plain ``enabled: false`` in a block is what turns one off
    (``core/extension.py``'s ``enabled`` = ``config.get('enabled', True)``). It is imported LAZILY and
    cached, and its failure is not fatal: a process that cannot import the bot's server layer (a bare
    web test) simply gets NO default marker — the honest answer, never a guess. The LOWER-CASED key is
    what the page compares names by; the VALUE is the spelling to SHOW (``Cloud``, not the folder's
    ``cloud``).
    """
    global _DEFAULT_EXTENSIONS
    if _DEFAULT_EXTENSIONS is None:
        try:
            from core.data.impl.serverimpl import DEFAULT_EXTENSIONS
            _DEFAULT_EXTENSIONS = {str(name).lower(): str(name) for name in DEFAULT_EXTENSIONS}
        except Exception:  # noqa: BLE001 - the marker is a nicety, never a render blocker
            return {}
    return _DEFAULT_EXTENSIONS
