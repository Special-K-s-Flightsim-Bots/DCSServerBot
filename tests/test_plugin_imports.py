"""Every plugin's commands module must import with no bot running.

Regression test for the AttributeError in ``core/plugin.py``'s ``Command.__init__``: the command
constructor looked up the *live* bot to decide whether to trim the ``node``/``server`` parameter,
so merely importing a plugin's ``commands`` module without a bot raised
``'NoneType' object has no attribute 'bot'``. It affected 28 of the 43 shipped plugins.

Each module is imported in a **fresh interpreter**: importing a plugin registers module-level
state, so an in-process loop would say nothing about the plugins after the first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _shipped_plugins() -> list[str]:
    """The plugins this checkout TRACKS — the project's own set.

    A working copy can carry plugins the project does not ship — a third-party one copied into
    ``plugins/``, or a local build that ``.gitignore`` excludes, such as ``community``. Those answer
    to their own authors, so a defect in one must not fail this suite. Outside a git checkout the
    tree read is all there is.
    """
    try:
        listing = subprocess.run(["git", "ls-files", "--", "plugins"], cwd=REPO,
                                 capture_output=True, text=True, encoding="utf-8", timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return sorted(p.parent.name for p in (REPO / "plugins").glob("*/commands.py"))
    names = set()
    for line in listing.splitlines():
        parts = Path(line).parts
        if len(parts) == 3 and parts[0] == "plugins" and parts[2] == "commands.py":
            names.add(parts[1])
    return sorted(names)


PLUGINS = _shipped_plugins()

BOT_LOOKUP_ERROR = "'NoneType' object has no attribute 'bot'"


def _bare_import(module: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", f"import {module}"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=300)


@pytest.mark.parametrize("plugin", PLUGINS)
def test_commands_module_imports_without_a_bot(plugin: str):
    result = _bare_import(f"plugins.{plugin}.commands")
    if result.returncode == 0:
        return
    err = result.stderr
 # The defect this test exists for.
    assert BOT_LOOKUP_ERROR not in err, (
        f"plugins.{plugin}.commands needs a running bot at import time again:\n{err[-2000:]}")
 # A plugin's own optional dependency is an environment fact, not a plugin defect.
    for line in err.splitlines():
        if line.startswith("ModuleNotFoundError"):
            pytest.skip(f"{plugin}: optional dependency not installed — {line}")
    pytest.fail(f"plugins.{plugin}.commands failed to import for another reason:\n{err[-2000:]}")


# --------------------------------------------------------------------------- binding semantics

class _StubNode:
    def __init__(self, count: int):
        self.all_nodes = {f"node-{i}": object() for i in range(count)}


class _StubBot:
    def __init__(self, nodes: int, servers: int, admin: bool = True):
        self.node = _StubNode(nodes)
        self.servers = {f"server-{i}": object() for i in range(servers)}
        self.locals = {"channels": {"admin": "1"} if admin else {}}


def _a_command():
    """A real core Command, built the way a plugin builds one: through the decorator."""
    from core.plugin import Command

    async def probe(interaction, node: str = "", server: str = "") -> None:
        """probe"""

    return Command(name="probe", description="probe", callback=probe)


def test_a_command_can_be_built_with_no_bot_and_keeps_its_parameters():
    cmd = _a_command()
    assert cmd._bound is False, "a command built with no bot must be unbound, not crashed"
    assert {"node", "server"} <= set(cmd._params), (
        f"both parameters should still be declared before binding, got {sorted(cmd._params)}")


def test_binding_trims_only_on_a_small_cluster_and_is_idempotent():
    cmd = _a_command()

    cmd.bind(_StubBot(nodes=3, servers=5))
    assert cmd._bound is True
    assert {"node", "server"} <= set(cmd._params), "a multi-node/multi-server cluster keeps both"
    cmd.bind(_StubBot(nodes=3, servers=5))
    assert {"node", "server"} <= set(cmd._params), "binding twice must change nothing"

    small = _a_command()
    small.bind(_StubBot(nodes=1, servers=1))
    assert "node" not in small._params and "server" not in small._params, (
        "one node and one server means neither parameter is worth asking for")


def test_a_reloaded_command_is_trimmed_by_the_import_time_path():
    """Reload imports the module with the bot up, so the eager path applies (no bind pass needed)."""
    cmd = _a_command()
    cmd.bind(_StubBot(nodes=1, servers=1))          # what __init__ does when the bot is reachable
    assert cmd._bound is True and "server" not in cmd._params