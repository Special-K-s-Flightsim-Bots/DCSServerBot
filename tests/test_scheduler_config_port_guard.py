"""The port guard compares the port NUMBER, not its type.

``UploadModal.on_submit`` merges every key of an uploaded ``serverSettings.lua`` straight into
``ServerConfigView.config`` (``plugins/scheduler/views.py``), so ``config['port']`` can hold the same
port in another type (an int, a float, or a numeric string) than ``server.settings['port']``. The
guard's ``!=`` was type-sensitive: a non-Admin who only UPLOADED a file was refused for a port
change they never made. The guard normalises both sides to an int before comparing; a value that is
not a whole number still counts as a change (fail-closed) and never raises out of ``on_save``.

The doubles are the ones ``test_scheduler_config_authz.py`` drives the real view with. The LIVE
panel in Discord is not exercised here — this drives the callback in-process.
"""
from __future__ import annotations

import types

import pytest

from core.server_config import MANAGER_DENIED

from test_scheduler_config_authz import Bot, ConfigServer, Interaction


def _save_view(admin: bool, settings: dict):
    """A real ``ServerConfigView`` over ``settings`` and the interaction that receives its answer."""
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    server = ConfigServer(settings=dict(settings))
    interaction = Interaction(bot)
    view = ServerConfigView(bot, server, admin=admin)
    return view, server, interaction


async def _save(view, interaction):
    from plugins.scheduler.views import ServerConfigView

    await ServerConfigView.on_save(view, interaction, types.SimpleNamespace())


# ------------------------------------- the guard is on the NUMBER: same number, another type — equal

@pytest.mark.asyncio
async def test_non_admin_same_port_as_float_is_not_refused_and_changes_nothing():
    """A float port equal to the stored int is not a change: no refusal, nothing observable written."""
    original = {"name": "SRS-1", "port": 10308, "maxPlayers": 16}
    view, server, interaction = _save_view(False, original)
    view.config["port"] = 10308.0  # what UploadModal.on_submit can merge in

    await _save(view, interaction)

    assert interaction.followup.messages == [], \
        "the same port number in another type is not a change, so no refusal is sent"
    assert server.settings == original, \
        "nothing observable was written: the port number is unchanged"


@pytest.mark.asyncio
async def test_non_admin_same_port_as_numeric_string_is_not_refused():
    """A numeric-string port equal to the stored int is not a change: no refusal."""
    view, server, interaction = _save_view(False, {"name": "SRS-1", "port": 10308, "maxPlayers": 16})
    view.config["port"] = "10308"  # what UploadModal.on_submit can merge in

    await _save(view, interaction)

    assert interaction.followup.messages == [], \
        "the same port number in another type is not a change, so no refusal is sent"
    assert int(server.settings["port"]) == 10308, "the port number is unchanged"


# ------------------------------------------------- the guard is still fail-closed on a real change

@pytest.mark.asyncio
@pytest.mark.parametrize("uploaded", [10400, 10400.0, "10400"])
async def test_non_admin_a_changed_port_is_refused_whatever_its_type(uploaded):
    """A port that is a different NUMBER is refused, writing nothing, in every type."""
    original = {"name": "SRS-1", "port": 10308, "maxPlayers": 16}
    view, server, interaction = _save_view(False, original)
    view.config["port"] = uploaded

    await _save(view, interaction)

    assert interaction.followup.messages == [MANAGER_DENIED["port"]], \
        "the answer is the declaration's own sentence"
    assert server.settings == original, "the WHOLE save is refused: nothing lands on the server"
    assert server.rename_calls == [] and server.reloaded == 0, "no rename, no reload"


@pytest.mark.asyncio
@pytest.mark.parametrize("uploaded", ["abc", "", 10308.75])
async def test_non_admin_a_non_numeric_port_is_refused_and_does_not_raise(uploaded):
    """A value that cannot be shown to be EQUAL counts as a change: refused, never raised."""
    original = {"name": "SRS-1", "port": 10308, "maxPlayers": 16}
    view, server, interaction = _save_view(False, original)
    view.config["port"] = uploaded

    await _save(view, interaction)  # must not raise

    assert interaction.followup.messages == [MANAGER_DENIED["port"]], \
        "an unconvertible port is fail-closed: refused, not accepted"
    assert server.settings == original, "nothing lands on the server"


# ---------------------------------------------------------------- the Admin path is not changed

@pytest.mark.asyncio
async def test_admin_a_changed_port_still_writes_the_uploaded_value():
    """The guard never runs for an Admin: the changed port is written exactly as it came."""
    view, server, interaction = _save_view(True, {"name": "SRS-1", "port": 10308, "maxPlayers": 16})
    view.config["port"] = "10400"

    await _save(view, interaction)

    assert interaction.followup.messages == [], "no refusal is sent to an Admin"
    assert server.settings["port"] == "10400", "the Admin's uploaded port is written as it came"
