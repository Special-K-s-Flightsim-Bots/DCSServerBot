"""The counts `core/ACTIONS.md` declares, checked against the live action registry.

The document states a per-plugin table of write/read/total counts plus an ``all`` row, and cites a
line anchor beside each registry piece it names. Both are facts about the tree, so both are parsed
here and compared to the code: a new `@action`, a removed one, or a shifted definition breaks this
test instead of leaving the prose quietly wrong.

The registry is PROCESS state filled by an IMPORT (a no-op the second time), so the fixture clears it,
drops the plugin modules from the cache so the decorators run again, and restores both afterwards.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from core import actions as actions_module

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "core" / "ACTIONS.md"
PLUGINS = REPO_ROOT / "plugins"

#: A count row: `| `plugin` | write | read | total |`, the summary row in bold.
_COUNT_ROW = re.compile(
    r"^\|\s*\*{0,2}`?(?P<name>[a-z_]+)`?\*{0,2}\s*\|"
    r"\s*\*{0,2}(?P<write>\d+)\*{0,2}\s*\|\s*\*{0,2}(?P<read>\d+)\*{0,2}\s*\|"
    r"\s*\*{0,2}(?P<total>\d+)\*{0,2}\s*\|\s*$")

#: An anchor row: `| `symbol` | path.py:NN | … |`.
ANCHOR_ROW = re.compile(r"^\|\s*`(\w+)`\s*\|\s*`([\w./]+\.(?:py|html|md)):(\d+)`",
                        re.MULTILINE)


def _plugin_modules() -> list[str]:
    """Every plugin directory that declares actions — the plugin names discovery is given."""
    return sorted(path.parent.name for path in PLUGINS.glob("*/actions.py"))


@pytest.fixture
def live_registry():
    """The action registry, discovered from scratch for this test."""
    saved = dict(actions_module.action_registry())
    saved_kinds = dict(actions_module._ACTION_KINDS)

    def drop_action_modules() -> None:
        for name in [n for n in sys.modules
                     if n.startswith("plugins.") and n.endswith(".actions")]:
            sys.modules.pop(name, None)

    actions_module._ACTIONS.clear()
    actions_module._ACTION_KINDS.clear()
    drop_action_modules()
    try:
        yield actions_module.discover_actions(_plugin_modules())
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTION_KINDS.clear()
        actions_module._ACTIONS.update(saved)
        actions_module._ACTION_KINDS.update(saved_kinds)
        drop_action_modules()


def _live_counts(registry) -> dict[str, dict[str, int]]:
    """``{plugin: {write, read, total}}` measured from the registry's own entries."""
    counts: dict[str, dict[str, int]] = {}
    for qualname, fn in registry.items():
        plugin = getattr(fn, "__module__", "").split(".")
        if len(plugin) < 2 or plugin[0] != "plugins":
            continue
        entry = counts.setdefault(plugin[1], {"write": 0, "read": 0, "total": 0})
        kind = actions_module._ACTION_KINDS.get(qualname, "write")
        entry["read" if kind == "read" else "write"] += 1
        entry["total"] += 1
    return counts


def _declared_counts(text: str) -> dict[str, dict[str, int]]:
    """Every count row of the document, keyed by plugin name (``all`` for the summary row)."""
    declared: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        match = _COUNT_ROW.match(line.strip())
        if match is None:
            continue
        declared[match.group("name")] = {key: int(match.group(key))
                                         for key in ("write", "read", "total")}
    return declared


def test_the_document_declares_the_live_per_plugin_counts(live_registry):
    """The per-plugin table is the registry's own split, plugin for plugin."""
    declared = _declared_counts(DOC.read_text(encoding="utf-8"))
    live = _live_counts(live_registry)

    assert declared, "core/ACTIONS.md declares no count table"
    assert set(declared) - {"all"} == set(live), (
        "the documented plugin set differs from the discovered one:\n"
        f"  documented only: {sorted(set(declared) - {'all'} - set(live))}\n"
        f"  live only:       {sorted(set(live) - set(declared))}")
    for plugin, counts in live.items():
        assert declared[plugin] == counts, f"{plugin}: documented {declared[plugin]}, live {counts}"


def test_the_document_declares_the_live_totals(live_registry):
    """The summary row is both the sum of the rows above it and the registry's own total."""
    declared = _declared_counts(DOC.read_text(encoding="utf-8"))
    assert "all" in declared, "core/ACTIONS.md declares no `all` row"
    summary = declared["all"]
    summed = {key: sum(row[key] for name, row in declared.items() if name != "all")
              for key in ("write", "read", "total")}
    live: dict[str, int] = {"write": 0, "read": 0, "total": len(live_registry)}
    for counts in _live_counts(live_registry).values():
        live["write"] += counts["write"]
        live["read"] += counts["read"]

    assert summary == summed, f"the `all` row {summary} is not the sum of the rows {summed}"
    assert summary == live, f"the `all` row {summary} is not the registry's own total {live}"


def test_every_line_anchor_points_at_its_symbol():
    """Each `| `symbol` | `path:NN` |` row cites a line that still carries that symbol."""
    text = DOC.read_text(encoding="utf-8")
    anchors = [(symbol, path, int(line))
               for symbol, path, line in ANCHOR_ROW.findall(text)]
    assert anchors, "core/ACTIONS.md declares no anchored registry table"

    wrong: list[str] = []
    for symbol, path, number in anchors:
        source = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
        if number > len(source):
            wrong.append(f"{path}:{number} is past the end of the file ({len(source)} lines)")
            continue
        if not re.search(rf"\b{re.escape(symbol)}\b", source[number - 1]):
            wrong.append(f"{path}:{number} does not carry `{symbol}`: "
                         f"{source[number - 1].strip()[:80]!r}")
    assert not wrong, "stale line anchors in core/ACTIONS.md:\n  " + "\n  ".join(wrong)
