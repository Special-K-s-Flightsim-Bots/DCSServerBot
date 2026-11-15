"""``@action`` declares the action's KIND — ``read`` or ``write`` — and the registry stays a mapping.

The kind is DOMAIN metadata: what the action does to the installation, not which HTTP shape reaches
it. It is what makes "a READ row never names a WRITE action" provable, so the marking is pinned here
by NAME and by COUNT — a swap of one read for one write keeps the count at 27 and must still fail.

The pins name the 27 reads and the 59 writes over ``plugins/*/actions.py`` (86 module-level ``@action``
functions, every name unique). The two fleet reads ``list_servers`` / ``get_server_status`` are part
of that census: they are real actions of the mission plugin, declared READ.
"""
from __future__ import annotations

import importlib

import pytest

from core import actions as actions_module
from core.actions import (
    ACTION_KIND_READ,
    ACTION_KIND_WRITE,
    ACTION_KINDS,
    action,
    action_registry,
    discover_actions,
    kind_of,
)

#: The 27 READ actions, by the module that declares them.
READ_ACTIONS: dict[str, tuple[str, ...]] = {
    "mission": ("list_missions", "get_mission_list", "get_mission_info", "read_mission_weather",
                "get_addable_missions", "download_mission", "list_bans", "list_player_directory",
                "get_player_detail", "get_player_statistics", "get_player_infractions",
                "search_players", "list_servers", "get_server_status"),
    "monitoring": ("get_server_statistics", "get_node_statistics", "get_load_statistics"),
    "music": ("list_music_playlists", "get_music_playlist", "read_music_library",
              "get_music_radios"),
    "modmanager": ("list_installed_mods", "list_available_mods", "list_repo_versions",
                   "list_mod_versions"),
    "scheduler": ("get_server_config", "get_server_coalitions"),
}

#: The 59 WRITE actions — every one of them an action whose decorator carries no ``kind``.
WRITE_ACTIONS: dict[str, tuple[str, ...]] = {
    "mission": ("start_server", "stop_server", "restart_server", "startup_server", "shutdown_server",
                "pause_mission", "unpause_mission", "restart_mission", "rotate_mission",
                "load_mission", "set_active_mission", "message_player", "message_server",
                "screenshot_player", "kick_player", "ban_player", "ban_ucid", "unban", "edit_ban",
                "watch_player", "unwatch_player", "edit_watch", "mute_player", "unmute_player",
                "upload_mission", "add_mission", "delete_mission", "delete_missions",
                "reorder_mission"),
    "admin": ("restart_node", "shutdown_node", "upgrade_node", "update_dcs", "take_node_offline",
              "bring_node_online", "add_server", "delete_server", "enable_extension",
              "disable_extension"),
    "modmanager": ("install_mod", "uninstall_mod", "change_mod_version", "download_mod"),
    "music": ("add_playlist_track", "remove_playlist_track", "reorder_playlist", "rename_playlist",
              "delete_playlist", "upload_music_track", "set_radio_playlist", "control_radio",
              "set_radio_config"),
    "punishment": ("punish_player", "forgive_player"),
    "scheduler": ("set_server_config", "set_server_channels", "set_coalition_password",
                  "set_maintenance", "clear_maintenance"),
}

#: Every plugin that ships an actions module — imported so its decorators actually run.
PLUGINS = ("admin", "mission", "modmanager", "monitoring", "music", "punishment", "scheduler")


def _read_names() -> set[str]:
    return {name for names in READ_ACTIONS.values() for name in names}


def _write_names() -> set[str]:
    return {name for names in WRITE_ACTIONS.values() for name in names}


@pytest.fixture
def installed_actions() -> dict:
    """Import every plugin's ``actions.py`` and hand back the registry they filled.

    The autouse registry fixture empties ``_ACTIONS`` and drops the ``plugins.*.actions`` modules, so
    the imports here re-run every decorator instead of answering from an earlier test's cache.
    """
    for plugin in PLUGINS:
        importlib.import_module(f"plugins.{plugin}.actions")
    return action_registry()


# --------------------------------------------------------------- 1. the marking, by name and count

def test_the_read_marked_actions_are_exactly_the_27(installed_actions):
    """The read set equals the census BY NAME — a count alone would hide a read/write swap."""
    reads = {qualname for qualname in installed_actions if kind_of(qualname) == ACTION_KIND_READ}

    assert reads == _read_names()
    assert len(reads) == 27
    for plugin, names in READ_ACTIONS.items():
        module_reads = {q for q in installed_actions if kind_of(q) == ACTION_KIND_READ
                        and installed_actions[q].__module__ == f"plugins.{plugin}.actions"}
        assert module_reads == set(names), f"{plugin}: read marking drifted"


def test_the_remaining_59_actions_report_write(installed_actions):
    """The 59 writes are unmarked decorators — they must answer ``write``, by name."""
    writes = {qualname for qualname in installed_actions if kind_of(qualname) == ACTION_KIND_WRITE}

    assert writes == _write_names()
    assert len(writes) == 59


def test_the_census_covers_every_registered_action(installed_actions):
    """86 registered, 27 read + 59 write, no name in both lists, no action left out."""
    assert len(installed_actions) == 86
    assert _read_names().isdisjoint(_write_names())
    assert _read_names() | _write_names() == set(installed_actions)


# --------------------------------------------------------------- 2. the decorator's two spellings

#: Undecorated module-level probes: a function's ``__qualname__`` is the registry key, so these must
#: live at module level to register under their plain name.
def _unmarked_probe(ctx):
    return True


def _read_probe(ctx):
    return True


def _write_probe(ctx):
    return True


def test_an_action_decorated_without_a_kind_reports_write():
    """A bare ``@action`` is a WRITE — the fail-safe default for an unmarked action."""
    action(_unmarked_probe)

    assert "_unmarked_probe" in action_registry()
    assert action_registry()["_unmarked_probe"] is _unmarked_probe
    assert kind_of("_unmarked_probe") == ACTION_KIND_WRITE


def test_an_explicit_read_reports_read_and_registers_the_same_way():
    """``@action(kind="read")`` registers exactly what the bare form registers, plus its kind."""
    assert action(kind="read")(_read_probe) is _read_probe
    assert action(_write_probe) is _write_probe

    assert action_registry()["_read_probe"] is _read_probe
    assert action_registry()["_write_probe"] is _write_probe
    assert kind_of("_read_probe") == ACTION_KIND_READ
    assert kind_of("_write_probe") == ACTION_KIND_WRITE


def test_a_kind_outside_the_two_raises_at_decoration_time():
    """A typo cannot register a third state: the decorator refuses anything but read/write."""

    assert ACTION_KINDS == (ACTION_KIND_READ, ACTION_KIND_WRITE)
    with pytest.raises(ValueError):
        action(kind="READ")


def test_kind_of_answers_write_for_a_name_that_is_not_registered():
    """An absent action declares nothing, and a caller must assume the fail-safe direction."""
    assert kind_of("no_such_action_anywhere") == ACTION_KIND_WRITE


# --------------------------------------------------------------- 3. the registry shape (consumers)

def test_action_registry_returns_the_same_mapping_it_returns_today(installed_actions):
    """The registry stays ``dict[str, Callable]`` — plain functions, no tuples and no wrappers."""
    registry = action_registry()

    assert isinstance(registry, dict)
    assert len(registry) == len(installed_actions)
    for qualname, fn in registry.items():
        assert isinstance(qualname, str)
        assert callable(fn)
        assert not isinstance(fn, tuple)
    assert registry == installed_actions


def test_action_registry_hands_back_a_copy(installed_actions):
    """A consumer that mutates what it got must not reach into the registry itself."""
    registry = action_registry()
    registry.clear()

    assert len(action_registry()) == len(installed_actions)
    assert kind_of("list_missions") == ACTION_KIND_READ


def test_kind_of_answers_for_every_registered_action(installed_actions):
    """No registered action is left without an answer — and never an exception."""
    for qualname in installed_actions:
        assert kind_of(qualname) in ACTION_KINDS


def test_discovery_returns_the_registry_and_the_kinds_survive_it():
    """``discover_actions`` keeps returning ``dict[str, Callable]``; the kinds ride beside it."""
    registry = discover_actions(["monitoring"])

    assert isinstance(registry, dict)
    assert "get_server_statistics" in registry
    assert registry["get_server_statistics"] is actions_module._ACTIONS["get_server_statistics"]
    assert kind_of("get_server_statistics") == ACTION_KIND_READ
