"""The console's write-action discovery must be LOUD about a failure and say what it found.

``core.actions.discover_actions`` caught ``ModuleNotFoundError`` around
``importlib.import_module(f"plugins.{name}.actions")`` and read EVERY one as "this plugin has no
actions module", so a plugin whose ``actions.py`` EXISTS but cannot import discovered NOTHING and every
write control was silently omitted -- indistinguishable on a live install from "the operator never
deployed the files". A non-``ModuleNotFoundError`` (a ``RuntimeError`` from the module body) escaped
the loop and took every LATER plugin's discovery down with it. The shell's own install line also
reported a COUNT but never the NAMES. The broken plugins are REAL modules on disk (a synthetic
``plugins`` package whose ``__path__`` also holds the real one), because mocking the import machinery
would leave the classification -- the whole point -- untested."""
from __future__ import annotations

import logging
import sys
import types

import pytest

from conftest import REPO_ROOT, create_app, install_shell

import core.actions as actions_module
from core.actions import discover_actions, action_registry

#: the REAL plugins directory — appended to the synthetic package's ``__path__`` so that the plugin
#: AFTER a broken one (``mission``) is still importable in the same test.
REAL_PLUGINS = REPO_ROOT / "plugins"

#: every action the ``mission`` plugin registers, exhaustively — the shell's summary line must name
#: exactly these, and the count in the same line is asserted against this list's length.
MISSION_ACTIONS = (
    "add_mission",
    "ban_player",
    "ban_ucid",
    "delete_mission",
    "delete_missions",
    "download_mission",
    "edit_ban",
    "edit_watch",
    "get_addable_missions",
    "get_mission_info",
    "get_mission_list",
    "get_player_detail",
    "get_player_infractions",
    "get_player_statistics",
    "get_server_status",
    "kick_player",
    "list_bans",
    "list_missions",
    "list_player_directory",
    "list_servers",
    "load_mission",
    "message_player",
    "message_server",
    "mute_player",
    "pause_mission",
    "read_mission_weather",
    "reorder_mission",
    "restart_mission",
    "restart_server",
    "rotate_mission",
    "screenshot_player",
    "search_players",
    "set_active_mission",
    "shutdown_server",
    "start_server",
    "startup_server",
    "stop_server",
    "unban",
    "unmute_player",
    "unpause_mission",
    "unwatch_player",
    "upload_mission",
    "watch_player",
)

MISSING_DEP_SOURCE = "import a_dependency_that_is_not_installed  # noqa: F401\n"
RUNTIME_ERROR_SOURCE = "raise RuntimeError('the actions module is broken')\n"


@pytest.fixture
def clean_action_registry():
    """The ``@action`` registry is PROCESS state, and discovery works by IMPORTING a module.

    Both are restored after every test: the registry is emptied so an assertion cannot pass on an
    earlier test's discovery, and ``plugins.mission.actions`` is dropped from the module cache so the
    decorators run again (the import is a no-op the second time in one process).
    """
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def _plugin(pkg, name, actions_source: str) -> None:
    plugin = pkg / name
    plugin.mkdir()
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "actions.py").write_text(actions_source, encoding="utf-8")


@pytest.fixture
def synthetic_plugins(tmp_path, monkeypatch):
    """A synthetic ``plugins`` package carrying two broken plugins, plus the REAL plugins dir.

    The import prefix is hardcoded (``plugins.<name>.actions``), so the only way to exercise a broken
    plugin for real is a package with that same name; extending its ``__path__`` keeps ``mission``
    importable alongside, which is what makes "the others still register" checkable.
    """
    root = tmp_path / "synth"
    pkg = root / "plugins"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f"__path__.append({str(REAL_PLUGINS)!r})\n", encoding="utf-8")
    _plugin(pkg, "broken_missing_dep", MISSING_DEP_SOURCE)
    _plugin(pkg, "broken_runtime", RUNTIME_ERROR_SOURCE)

 # the real ``plugins`` package must not shadow the synthetic one (its cached ``__path__`` would
 # win, and ``plugins.broken_missing_dep`` would simply not be found — a green test measuring
 # nothing). Both are restored afterwards.
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


def discovery_warnings(caplog):
    """The WARNING records discovery itself emitted (never another logger's)."""
    return [record for record in caplog.records
            if record.name == "core.actions" and record.levelno == logging.WARNING]


# --------------------------------------------------- an existing actions module that fails to import

def test_a_missing_dependency_inside_an_existing_actions_module_is_logged(synthetic_plugins,
                                                                         clean_action_registry,
                                                                         caplog):
    """THE bug: a ``ModuleNotFoundError`` from INSIDE an existing module used to read as "no
    actions module here — fine", so a live log carried NO line at all about it."""
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        discover_actions(["broken_missing_dep"])

    warnings = discovery_warnings(caplog)
    assert len(warnings) == 1, "a plugin whose actions.py exists but cannot import must be LOUD"
    message = warnings[0].getMessage()
    assert "broken_missing_dep" in message, "the operator needs the plugin's name"
    assert "plugins.broken_missing_dep.actions" in message, "…and the module it tried"
    assert warnings[0].exc_info, "the traceback must be logged, not merely the fact of a failure"


def test_an_actions_module_that_raises_anything_else_is_logged_too(synthetic_plugins,
                                                                 clean_action_registry,
                                                                 caplog):
    """The other spelling of the same failure: the module EXISTS and its body raises. It used to
    escape the ``except ModuleNotFoundError`` entirely and abort the whole discovery loop."""
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        discover_actions(["broken_runtime"])

    warnings = discovery_warnings(caplog)
    assert len(warnings) == 1
    assert "broken_runtime" in warnings[0].getMessage()
    assert warnings[0].exc_info


def test_a_broken_actions_module_does_not_stop_another_plugin_from_registering(
        synthetic_plugins, clean_action_registry, caplog):
    """Two broken plugins, then a healthy one: the failure is per-plugin, never fatal."""
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        registry = discover_actions(["broken_missing_dep", "broken_runtime", "mission"])

    assert len(discovery_warnings(caplog)) == 2
 # the healthy plugin AFTER both failures still registered — the property the loop used to lose
    assert "pause_mission" in registry
    assert "start_server" in registry


# ------------------------------------------------------------- a plugin with no actions module

def test_a_plugin_without_an_actions_module_stays_quiet(clean_action_registry, caplog):
    """A plugin that genuinely has no actions module is ORDINARY: not a single line.

    The positive control for the two tests above — a warning that fires for every plugin would make
    them pass for the wrong reason.
    """
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        discover_actions(["monitoring"])

    assert discovery_warnings(caplog) == []
    assert "pause_mission" not in action_registry(), "no action may be invented from nothing"


def test_a_plugin_that_is_not_deployed_at_all_stays_quiet(clean_action_registry, caplog):
    """The other half of "absent": not even the plugin PACKAGE is there.

    ``ModuleNotFoundError.name`` is then the package (``plugins.<name>``), not the actions module, and
    it is still the ordinary case — a plugin that is simply not on this install must not be reported
    as a broken one.
    """
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        discover_actions(["no_such_plugin_at_all"])

    assert discovery_warnings(caplog) == []


# ------------------------------------------------------------- the shell's own install line

def test_the_shells_install_line_names_the_actions_it_found(tmp_path, clean_action_registry, caplog):
    """ACCEPTANCE: the install path states this ONCE, at DEBUG, with the count AND the names, in
    stable wording an operator can grep verbatim in a live log.

    The INFO line for the same registry is the bot's own (``core.plugin_manager``) and carries the
    COUNTS only, so a start prints the count sentence once per process and the name block never on
    INFO — the 38 names wrapped the log twice before.
    """
    app = create_app()
    node = types.SimpleNamespace(master=True, plugins=["mission"])

    with caplog.at_level(logging.DEBUG, logger="services.webservice.shell"):
        install_shell(app, node=node, config={})

    lines = [record.getMessage() for record in caplog.records
             if record.name == "services.webservice.shell"
             and record.getMessage().startswith("Admin web shell: action registry populated")]
    assert len(lines) == 1, "exactly one summary line — not one per plugin"
    assert lines[0] == (
        "Admin web shell: action registry populated from 1 plugin(s) - "
        f"{len(MISSION_ACTIONS)} action(s) available in this process: "
        + ", ".join(sorted(MISSION_ACTIONS)) + "."
    )
    assert [r.levelno for r in caplog.records
            if r.name == "services.webservice.shell"
            and "action registry populated" in r.getMessage()] == [logging.DEBUG], (
        "the name block belongs to DEBUG: INFO must stay short")
 # the count in the line is the registry's own, not a second opinion
    assert set(action_registry()) == set(MISSION_ACTIONS)
