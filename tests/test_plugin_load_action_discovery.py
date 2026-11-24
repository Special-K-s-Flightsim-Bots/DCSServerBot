"""The action registry must be populated when the plugins load, not by a transport.

``core.actions.discover_actions`` was only called by the web shell and the MCP service, so with
``frontend: false`` the registry stayed empty and every Discord command delegating to an action
answered a typed refusal. The authoritative populate step is ``PluginManager.load_plugins`` (reached
by the real and headless bots alike, and re-run on a reload). These tests drive ``PluginManager``
with the frontend disabled, and pin that the fix does not duplicate registrations, that a reload is
idempotent, and that a plugin whose ``actions.py`` raises never takes the bot down."""
from __future__ import annotations

import logging
import sys
import types

import pytest

from core import actions as actions_module
from core.actions import action_available, action_registry, reset_in_flight, discover_actions

#: the actions the plugins actually register — asserted by name so a test cannot pass on an invented
#: entry.
SCHEDULER_ACTION = "set_maintenance"
ADMIN_ACTION = "bring_node_online"
MISSION_ACTION = "pause_mission"


@pytest.fixture
def clean_action_registry():
    """The ``@action`` registry is PROCESS state, and discovery works by IMPORTING a module.

    Both are restored after every test: the registry is emptied so a discovery assertion cannot pass
    on an earlier test's registration, and every ``plugins.*.actions`` module is dropped from the
    cache so the decorators run again (an import is a no-op the second time in one process).
    """
    def drop_action_modules():
        for name in [n for n in sys.modules
                     if n.startswith("plugins.") and n.endswith(".actions")]:
            sys.modules.pop(name, None)

    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    drop_action_modules()
    reset_in_flight()
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        drop_action_modules()
        reset_in_flight()


def a_master_node(plugins):
    """The minimum a MASTER ``PluginManager`` reads: ``master`` and the configured plugin list.

    The master path loads no event listeners and reads no ``config_dir``, so a master node never
    needs a bus, a pool or a cluster — the plugin-load wiring is exercised in isolation.
    """
    return types.SimpleNamespace(master=True, plugins=list(plugins))


# ----------------------------------------------------- 1. the load path with no shell installed

@pytest.mark.asyncio
async def test_the_plugin_load_registers_actions_with_no_shell_installed(clean_action_registry):
    """With the frontend disabled the plugin load must still fill the registry.

    No shell is installed anywhere in this test: the ONLY caller that runs is
    ``PluginManager.load_plugins``.
    """
    from core.plugin_manager import PluginManager

    assert action_available(SCHEDULER_ACTION) is False, "the registry must start EMPTY for this test"

    await PluginManager(a_master_node(["scheduler"])).load_plugins(["scheduler"])

    assert action_available(SCHEDULER_ACTION) is True, (
        "the plugin load must populate the action registry, or every Discord command that "
        "delegates to an action answers 'not available in this installation' when the frontend "
        "is disabled")
    assert SCHEDULER_ACTION in action_registry(), "the entry is the plugin's, not a hand-written one"


@pytest.mark.asyncio
async def test_every_loaded_plugin_contributes_its_actions(clean_action_registry):
    """Both plugins are discovered, from ONE load call."""
    from core.plugin_manager import PluginManager

    await PluginManager(a_master_node(["scheduler", "admin"])).load_plugins(["scheduler", "admin"])

    assert action_available(SCHEDULER_ACTION) is True
    assert action_available(ADMIN_ACTION) is True


@pytest.mark.asyncio
async def test_no_plugins_means_no_actions(clean_action_registry):
    """The CONTROL: discovery measures the plugin list, it does not invent entries.

    A wiring assertion that would pass for any input is not an assertion — an empty load must leave
    the registry empty.
    """
    from core.plugin_manager import PluginManager

    await PluginManager(a_master_node([])).load_plugins([])

    assert action_registry() == {}


# ----------------------------------------------------------- 2. a reload re-runs it, without doubles

@pytest.mark.asyncio
async def test_reloading_the_plugins_does_not_duplicate_the_registry(clean_action_registry):
    """A master/agent switch (or any second ``load_plugins``) re-runs discovery with no duplicates.

    ``importlib`` caches each ``actions.py`` and ``@action`` keys on ``__qualname__``, so the second
    pass must observe the SAME functions under the same keys — never a growing registry.
    """
    from core.plugin_manager import PluginManager

    manager = PluginManager(a_master_node(["scheduler", "admin"]))
    await manager.load_plugins(["scheduler", "admin"])
    first = action_registry()
    assert SCHEDULER_ACTION in first and ADMIN_ACTION in first

    await manager.load_plugins(["scheduler", "admin"])       # the reload / switch

    after = action_registry()
    assert after == first, "a reload must not add or replace entries"
    assert len(after) == len(first)


@pytest.mark.asyncio
async def test_a_plugin_load_twice_over_stays_stable_even_for_a_brand_new_manager(
        clean_action_registry):
    """Two independent managers (a master that switched role) load the same plugins: still one each.

    This is the second spelling of the same property — the registry is keyed, so two load calls from
    two objects cannot produce two entries for one action.
    """
    from core.plugin_manager import PluginManager

    await PluginManager(a_master_node(["scheduler"])).load_plugins(["scheduler"])
    one = len(action_registry())
    await PluginManager(a_master_node(["scheduler"])).load_plugins(["scheduler"])
    assert len(action_registry()) == one


# --------------------------------------------- 3. a broken plugin's actions.py is never fatal

@pytest.fixture
def synthetic_plugins(tmp_path, monkeypatch):
    """A synthetic ``plugins`` package carrying a plugin whose ``actions.py`` raises on import,
    plus the REAL plugins dir (so ``mission`` is importable in the same load)."""
    real_plugins = __import__("pathlib").Path(__file__).resolve().parent.parent / "plugins"

    root = tmp_path / "synth"
    pkg = root / "plugins"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f"__path__.append({str(real_plugins)!r})\n", encoding="utf-8")
    broken = pkg / "broken_runtime"
    broken.mkdir()
    (broken / "__init__.py").write_text("", encoding="utf-8")
    (broken / "actions.py").write_text("raise RuntimeError('the actions module is broken')\n",
                                       encoding="utf-8")

    saved = {name: mod for name, mod in sys.modules.items()
             if name == "plugins" or name.startswith("plugins.")}
    for name in list(saved):
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(root))
    try:
        yield root
    finally:
        for name in [n for n in sys.modules if n == "plugins" or n.startswith("plugins.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.mark.asyncio
async def test_a_broken_actions_module_never_stops_the_bot_or_the_other_plugins(
        synthetic_plugins, clean_action_registry, caplog):
    """A plugin whose ``actions.py`` explodes: the load returns, and its SIBLING still registers.

    The bot must stay alive — ``load_plugins`` may not raise — and ``mission`` (loaded AFTER the
    broken plugin) must still contribute its actions.
    """
    from core.plugin_manager import PluginManager

    with caplog.at_level(logging.WARNING, logger="core.actions"):
        await PluginManager(a_master_node(["broken_runtime", "mission"])).load_plugins(
            ["broken_runtime", "mission"])

    assert action_available(MISSION_ACTION) is True, \
        "one broken plugin must not cost the others their discovery"
    loud = [record for record in caplog.records
            if record.name == "core.actions" and record.levelno == logging.WARNING]
    assert any("broken_runtime" in record.getMessage() for record in loud), \
        "the failure must be loud — a silent omission is indistinguishable from a plugin with no actions"


# ------------------------------------- 4. the frontend-enabled path keeps working (belt and braces)

@pytest.mark.asyncio
async def test_the_web_shell_and_the_plugin_load_agree_on_one_registry(tmp_path, clean_action_registry):
    """With the frontend ENABLED, the shell's discovery and the plugin load do not disagree.

    The shell's call is the fallback for a shell installed without a bot plugin load; when both run
    they must produce ONE registry (both paths read the same plugin list and ``@action`` keys on
    ``__qualname__``), never a doubled one.
    """
    from conftest import create_app, install_shell
    from core.plugin_manager import PluginManager

    node = a_master_node(["scheduler", "admin"])
    node.config_dir = str(tmp_path)

    install_shell(create_app(), node=node, config={})          # the web path (frontend enabled)
    after_shell = action_registry()
    assert SCHEDULER_ACTION in after_shell

    await PluginManager(node).load_plugins(["scheduler", "admin"])   # ...then the plugin load

    after_load = action_registry()
    assert after_load == after_shell, "the plugin load must not register a second copy"


def test_discover_actions_is_idempotent_for_the_mission_plugin(clean_action_registry):
    """The mechanism the whole fix rests on, pinned directly: calling discovery twice is stable.

    ``@action`` overwrites by ``__qualname__`` and the module is imported once, so a second
    ``discover_actions`` leaves the registry unchanged — no new keys, no replaced functions.
    """
    first = discover_actions(["mission"])
    assert MISSION_ACTION in first
    second = discover_actions(["mission"])
    assert second == first, "discovery must be idempotent (the plugin-load reload depends on it)"
