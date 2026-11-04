"""The PLUGIN REGISTRY — which plugins exist on disk, what each one is for, and which are default.

WHY THIS EXISTS
---------------
``main.yaml``'s ``opt_plugins`` is a plain list of strings, so the Settings page used to render it as
a bare row-list: names only, no hint of what a name DOES or whether it is a plugin the bot already
loads by default. the operator asked for a dedicated block that gives an operator that context without a
hand-written table to keep in sync:

* the plugins are DISCOVERED from the tree (the same folders the bot's loader imports,
  ``plugins/<name>/commands.py`` — see :func:`discover_plugins`), never a hard-coded list;
* each one's one-line description is read from the plugin's OWN files (its ``README.md`` first
  sentence, falling back to its ``commands.py`` docstring) — never a table maintained here;
* the default/optional marker comes from the bot's OWN default set
  (:func:`default_plugins` reads ``core.data.impl.nodeimpl.DEFAULT_PLUGINS``); and
* :func:`build_block` turns that plus the file's CURRENT ``opt_plugins`` value into the rows the
  template renders — one row per shipped plugin, PLUS one for every entry already in the file that
  no longer maps to a shipped plugin (an uninstalled/legacy entry must stay visible and is reported,
  never silently dropped on save).

NO WRITE PATH HERE. This module only READS the tree and builds a render model. The block's checkboxes
post plain ``f.opt_plugins`` fields, which the page's ONE save path (``configwrite.patch_config_file``
through ``configgen.apply_form``) applies exactly like the schema-generated list it replaces.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from . import i18n

__all__ = [
    "PLUGINS_DIR", "COMMANDS_FILE", "OPT_PLUGINS_FIELD", "DEFAULT_FIELD",
    "DEFAULT_BADGE", "OPTIONAL_BADGE", "MISSING_BADGE", "BLOCK_KIND", "BLOCK_HINT",
    "PluginInfo", "PluginRow", "default_plugins", "discover_plugins", "build_block",
    "build_tiles",
]

#: the directory the bot loads its plugins from, addressed absolutely from THIS file so the registry
#: never depends on the process working directory (``services/webservice/plugin_registry.py`` ->
#: repo root -> ``plugins/``).
PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"

#: the module the bot's loader imports for every plugin (``plugins.<name>.commands`` — see
#: ``services/bot/dcsserverbot.py`` ``load_plugin``). A directory WITHOUT it is not a loadable plugin
#: (e.g. ``plugins/federation``, which only carries an ``__init__.py``), so it is not a row.
COMMANDS_FILE = "commands.py"

#: the config key this block edits. The block's checkboxes are named ``f.opt_plugins`` so the page's
#: one save path places them through the SCHEMA's own ``opt_plugins`` field (a ``seq`` of ``str``) —
#: there is no second list and no second write.
OPT_PLUGINS_FIELD = "opt_plugins"

#: THE OTHER LIST: the config key the bot's DEFAULT plugins live in. A **default** plugin is loaded
#: when its name is in ``main.yaml``'s ``plugins`` list — the loader is
#: ``self.config.get('plugins', DEFAULT_PLUGINS)`` (``core/data/impl/nodeimpl.py``), so the key being
#: ABSENT means every default (``DEFAULT_PLUGINS``) loads, and ``opt_plugins`` only ADDS optional
#: plugins on top. The landing TILES read a default plugin's on/off from THIS list, not from
#: ``opt_plugins``, so a default's switch reflects what the loader actually does.
DEFAULT_FIELD = "plugins"

#: The chrome the block shows. Born wrapped (``i18n._``) like every other composed word and filled in
#: every catalog; the DESCRIPTION is the plugin's own data and is never translated.
DEFAULT_BADGE = i18n._("default")
OPTIONAL_BADGE = i18n._("optional")
MISSING_BADGE = i18n._("not installed")
BLOCK_KIND = i18n._("optional plugins")
BLOCK_HINT = i18n._(
    "Tick the optional plugins to load. The ones marked \"default\" are part of the bot's default "
    "set and load even without an entry here. The raw list is still editable in Expert Mode.")


@dataclass(frozen=True)
class PluginInfo:
    """One plugin that EXISTS on disk: its folder name, its own one-line description, and whether it
    is part of the bot's default set."""

    name: str
    description: str
    default: bool


@dataclass(frozen=True)
class PluginRow:
    """One row of the block: a plugin (or a legacy entry), its description, its marker and its state.

    ``name`` is the exact string the config carries for a CURRENT entry (case preserved) or the
    plugin's own folder name for one not yet listed. ``installed`` is False for an entry in the file
    that maps to no shipped plugin — it is shown and reported, never dropped. ``enabled`` is whether
    the name is in the file's ``opt_plugins`` list today (the checkbox's initial state).
    """

    name: str
    description: str
    default: bool
    installed: bool
    enabled: bool
    badge: str
    #: THE LIST THIS ROW TOGGLES — ``plugins`` for a default plugin (the loader's default list),
    #: ``opt_plugins`` for an optional one. The tile posts the row's OWN field, so a default's switch
    #: writes the ``plugins`` list and an optional's writes ``opt_plugins``. Defaults to
    #: ``opt_plugins`` so the main.yaml block (which edits only ``opt_plugins``) is unchanged.
    field: str = OPT_PLUGINS_FIELD
    #: Whether turning this row OFF must FIRST show the inline warning — True only for a DEFAULT
    #: plugin (the one class whose removal can change how the bot works).
    warn: bool = False


def default_plugins() -> list[str]:
    """The bot's OWN default plugin set, read from the source of truth.

    ``DEFAULT_PLUGINS`` lives in ``core/data/impl/nodeimpl.py`` (the node's plugin list is
    ``config.get('plugins', DEFAULT_PLUGINS)``). It is imported LAZILY and its failure is not fatal:
    a process that cannot import the bot's node layer (a bare web test) simply gets NO default
    marker, which is the honest answer — the block never guesses which plugins are default.
    """
    try:
        from core.data.impl.nodeimpl import DEFAULT_PLUGINS
        return [str(name).lower() for name in DEFAULT_PLUGINS]
    except Exception:  # noqa: BLE001 - the marker is a nicety, never a render blocker
        return []


#: A markdown link ``[text](url)`` -> ``text``; an HTML tag (``<br>``, ``</br>`` …) -> nothing.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    """One readable line from a plugin's own prose: links flattened, tags and emphasis dropped."""
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("`", "").replace("*", "").replace("_", " ")
    return _WS.sub(" ", text).strip()


def _readme_description(plugin_dir: Path) -> str:
    """The first descriptive line of the plugin's ``README.md`` — its opening sentence.

    Heading lines (``#``), blockquotes/notes (``>``), table rows (``|``) and rules (``---``/``***``)
    are skipped, so what is returned is the prose a reader sees as the plugin's summary.
    """
    path = plugin_dir / "README.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
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


def _docstring_description(plugin_dir: Path) -> str:
    """The plugin's own docstring — its ``commands.py`` module or ``Plugin`` subclass docstring.

    Parsed with :mod:`ast`, never imported: rendering the page must not import every plugin (they pull
    ``discord``, ``psycopg`` and the bot itself), and a plugin that would fail to import still shows a
    description. The first line of the first docstring found is used.
    """
    path = plugin_dir / COMMANDS_FILE
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ""
    for doc in (ast.get_docstring(tree), *(_class_docstring(node) for node in tree.body)):
        if doc:
            first = _clean(doc.splitlines()[0]) if doc.splitlines() else ""
            if first:
                return first
    return ""


def _class_docstring(node: ast.AST) -> str | None:
    """The docstring of a class node, or ``None`` when it is not a class (or has no docstring)."""
    if isinstance(node, ast.ClassDef):
        return ast.get_docstring(node)
    return None


def _description(plugin_dir: Path) -> str:
    """A plugin's one-line description from its OWN files — README first, then its docstring."""
    return _readme_description(plugin_dir) or _docstring_description(plugin_dir)


def discover_plugins(plugins_dir: Path = PLUGINS_DIR) -> list[PluginInfo]:
    """Every plugin that EXISTS on disk, in name order — a reading of the tree, never a list.

    A plugin is a DIRECTORY under *plugins_dir* that carries a ``commands.py`` (the module the bot
    loads). ``__pycache__`` and dotted folders are skipped by that test naturally, as is a
    plugin-less folder such as ``federation`` (no ``commands.py``). The default marker comes from the
    bot's own default set (:func:`default_plugins`). A missing ``plugins`` directory yields nothing.
    """
    plugins_root = Path(plugins_dir)
    if not plugins_root.is_dir():
        return []
    defaults = set(default_plugins())
    found: list[PluginInfo] = []
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir() or child.name.startswith("__") or child.name.startswith("."):
            continue
        if not (child / COMMANDS_FILE).is_file():
            continue
        found.append(PluginInfo(name=child.name, description=_description(child),
                                default=child.name.lower() in defaults))
    return found


def build_block(entries, present: bool, plugins: list[PluginInfo] | None = None, *,
                plugins_dir: Path = PLUGINS_DIR) -> dict:
    """The plugin block as data: one row per shipped plugin PLUS one per existing (unmatched) entry.

    *entries* is the file's CURRENT ``opt_plugins`` value (a list, or ``None``); *present* says whether
    the key is there at all — the template needs it to emit the empty-list sentinel only when the key
    exists, so an untouched save never CREATES an absent ``opt_plugins`` key. *plugins* defaults to
    :func:`discover_plugins` (a caller may pass a fixed set for a test).

    Order: the file's OWN entries first, in file order (so an untouched save reproduces the list
    byte-for-byte), each checked; then the shipped plugins the file does not list, default-set ones
    before the rest, each unchecked. An entry that maps to no shipped plugin keeps its exact string,
    is checked (so it survives a save unless the operator clears it) and is reported in ``unknown``.
    """
    if plugins is None:
        plugins = discover_plugins(plugins_dir)
    by_name = {info.name.lower(): info for info in plugins}
    current = [str(item) for item in entries] if isinstance(entries, list) else []
    rows: list[PluginRow] = []
    seen: set[str] = set()
    for name in current:
        info = by_name.get(name.lower())
        rows.append(PluginRow(
            name=name,
            description=info.description if info else "",
            default=bool(info.default) if info else False,
            installed=info is not None,
            enabled=True,
            badge=(DEFAULT_BADGE if info and info.default else
                   (OPTIONAL_BADGE if info else MISSING_BADGE))))
        seen.add(name.lower())
    rest = [info for info in plugins if info.name.lower() not in seen]
    rest.sort(key=lambda info: (0 if info.default else 1, info.name))
    for info in rest:
        rows.append(PluginRow(name=info.name, description=info.description, default=info.default,
                              installed=True, enabled=False,
                              badge=DEFAULT_BADGE if info.default else OPTIONAL_BADGE))
    return {
        "rows": [row.__dict__ for row in rows],
        "unknown": [row.name for row in rows if not row.installed],
        "has_key": bool(present),
        "kind": BLOCK_KIND,
        "hint": BLOCK_HINT,
        "field": OPT_PLUGINS_FIELD,
    }


def build_tiles(plugins_value, plugins_present, opt_value, opt_present,
                plugins: list[PluginInfo] | None = None, *,
                plugins_dir: Path = PLUGINS_DIR) -> dict:
    """The landing page's plugin tiles as data — the DEFAULT list and the OPTIONAL list together.

    A plugin is ONE tile. Which LIST it reads its state from follows the bot's loader
    (``core/data/impl/nodeimpl.py``): a **default** plugin (a name in ``DEFAULT_PLUGINS``) is
    enabled when its name is in ``main.yaml``'s ``plugins`` — ``config.get('plugins',
    DEFAULT_PLUGINS)`` — while an **optional** plugin is enabled when its name is in ``opt_plugins``.
    So a default plugin's checkbox is ON when it is in ``plugins`` OR (the key being absent) it is a
    default at all, and its switch writes the ``plugins`` list; an optional's writes ``opt_plugins``.

    Order — the file's OWN entries first, in file order, so an untouched toggle reproduces the list
    byte-for-byte — the ``plugins`` entries (then the shipped defaults not named), then the
    ``opt_plugins`` entries (then the shipped non-defaults not named). An entry that maps to no
    shipped plugin keeps a row and is reported (:func:`default_plugins` never guesses a marker for
    it), never dropped.

    Returns the tile ``rows`` (each a :class:`PluginRow` as a dict, carrying its own ``field`` and
    ``warn``), the ``unknown`` names, and the RAW ``core``/``opt`` lists with their presence flags —
    the caller computes each row's toggle target from THOSE lists, not from the rendered rows, so an
    entry with no tile of its own (a default named in ``opt_plugins``, an unmatched entry) is never
    silently dropped when another tile is toggled.
    """
    if plugins is None:
        plugins = discover_plugins(plugins_dir)
    by_name = {info.name.lower(): info for info in plugins}
    core = [str(item) for item in plugins_value] if isinstance(plugins_value, list) else []
    opt = [str(item) for item in opt_value] if isinstance(opt_value, list) else []

    rows: list[PluginRow] = []
    seen: set[str] = set()

    def _add(name: str, field: str, enabled: bool) -> None:
        key = name.lower()
        info = by_name.get(key)
        default = bool(info.default) if info else False
        badge = DEFAULT_BADGE if default else (OPTIONAL_BADGE if info else MISSING_BADGE)
        rows.append(PluginRow(name=name, description=info.description if info else "",
                              default=default, installed=info is not None, enabled=enabled,
                              badge=badge, field=field, warn=default))
        seen.add(key)

    # 1. THE DEFAULT LIST — its state comes from `plugins` (or DEFAULT_PLUGINS when the key is absent).
    for name in core:
        if name.lower() not in seen:
            _add(name, DEFAULT_FIELD, True)                  # it is in the list, so it loads
    for info in sorted(plugins, key=lambda item: item.name):
        if info.default and info.name.lower() not in seen:
            _add(info.name, DEFAULT_FIELD, not plugins_present)   # implicit default: on only if absent
    # 2. THE OPTIONAL LIST — its state comes from `opt_plugins`.
    for name in opt:
        if name.lower() not in seen:                         # a default named here is ONE tile (above)
            _add(name, OPT_PLUGINS_FIELD, True)
    for info in sorted(plugins, key=lambda item: item.name):
        if not info.default and info.name.lower() not in seen:
            _add(info.name, OPT_PLUGINS_FIELD, False)

    return {
        "rows": [row.__dict__ for row in rows],
        "unknown": [row.name for row in rows if not row.installed],
        "core": core,
        "core_has_key": bool(plugins_present),
        "opt": opt,
        "opt_has_key": bool(opt_present),
    }
