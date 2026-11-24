"""The one player-slot rule: every surface shows ``players / (maxPlayers - 1)``.

The bot's own observer client holds one slot of ``settings.maxPlayers``, so a server advertising
the configured maximum is offering a seat nobody can join. The rule lives in
``core.utils.helper.player_slots`` and every surface that renders a player count calls it — the
counts drifted when each surface decided for itself.
"""
from __future__ import annotations

import pathlib

import pytest

from core.utils.helper import player_slots

REPO = pathlib.Path(__file__).resolve().parent.parent

#: every surface that renders a player count, and therefore has to use the one rule.
#: ``plugins/restapi`` is NOT here yet: that module in this tree carries an unlanded auth refactor,
#: so its ``max_players`` still reports the configured value.
DISPLAY_SURFACES = (
    "plugins/mission/commands.py",                 # Discord channel name
    "plugins/mission/status.py",                   # status embed author line, /server status all
    "plugins/scheduler/commands.py",               # All Servers embed
    "extensions/cloud/extension.py",               # the count published to the cloud
    "services/webservice/readmodels/servers.py",   # the website
    "services/dashboard/service.py",               # the terminal dashboard
)


@pytest.mark.parametrize("configured, slots", [
    (32, 31),
    (61, 60),
    (16, 15),
    (2, 1),
    (1, 1),
    (0, 1),
    (None, 1),
    ("16", 15),
    ("", 1),
])
def test_player_slots(configured, slots):
    assert player_slots(configured) == slots


def test_every_display_surface_uses_the_one_rule():
    """A surface that formats the configured value itself is the drift coming back."""
    offenders = [rel for rel in DISPLAY_SURFACES
                 if "player_slots" not in (REPO / rel).read_text(encoding="utf-8")]
    assert not offenders, (
        "these surfaces render a player count without calling utils.player_slots(): "
        + ", ".join(offenders))


def test_the_dashboard_renders_the_slot_count_not_the_configured_maximum():
    from services.dashboard.service import _max_players

    class _Server:
        settings = {"maxPlayers": 32}

    assert _max_players(_Server()) == 31
    _Server.settings = {"maxPlayers": 61}
    assert _max_players(_Server()) == 60, "the 61 case is the general rule, not a special case"
