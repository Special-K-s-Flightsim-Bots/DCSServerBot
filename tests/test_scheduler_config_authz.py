"""A non-Admin may not change a server's port or its Discord channels from ``/server config``.

The panel's Save and its three channel selects write straight to the server, bypassing the guarded
write actions; the manager deny-list (``core.server_config.MANAGER_DENIED``) was read only by those
actions. The real ``ServerConfigView`` is driven with doubles: a non-Admin's controls are locked AND
a change that still arrives is refused with the declaration's own sentence, writing nothing; an
Admin's ordinary path is unchanged.

The LIVE panel in Discord is not exercised here — this drives the callbacks in-process.
"""
from __future__ import annotations

import types

import pytest

from core.server_config import CHANNELS_ITEM, MANAGER_DENIED


# -------------------------------------------------------------------------------------- doubles

class ConfigServer:
    """The slice of a ``Server`` the panel reads and writes: name, settings and ``locals``."""

    def __init__(self, *, name: str = "SRS-1", settings: dict | None = None, locals: dict | None = None):
        self.name = name
        self.display_name = name
        self.settings = dict(settings or {})
        self.locals = dict(locals or {})
        self.rename_calls: list[tuple] = []
        self.reloaded = 0

    async def rename(self, new_name, update_settings=False):
        self.rename_calls.append((new_name, update_settings))
        self.name = new_name

    async def reload(self):
        self.reloaded += 1


class Response:
    def __init__(self):
        self.deferrals: list[dict] = []

    async def defer(self, *, thinking=False, ephemeral=False):
        self.deferrals.append({"thinking": thinking, "ephemeral": ephemeral})


class Followup:
    def __init__(self):
        self.messages: list = []

    async def send(self, content=None, **kwargs):
        self.messages.append(content)


class Interaction:
    def __init__(self, bot):
        self.client = bot
        self.response = Response()
        self.followup = Followup()
        self.user = types.SimpleNamespace(id=1, display_name="member")


class Bot:
    """The slice of the bot the view reads: its own ``locals`` and the server registry."""

    def __init__(self):
        self.locals: dict = {}
        self.servers: dict = {}
        self.owner_id = 1


def _select(channel_id: int = 777) -> types.SimpleNamespace:
    return types.SimpleNamespace(values=[types.SimpleNamespace(id=channel_id)])


# --------------------------------------------------------------- a non-Admin: locked AND refused

def test_non_admin_controls_are_locked():
    """The three channel selects are disabled and the port field is absent from the main modal."""
    from plugins.scheduler.views import MainModal, ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308})
    view = ServerConfigView(bot, server, admin=False)

    assert [view.children[i].disabled for i in (0, 1, 2)] == [True, True, True], \
        "every channel select is disabled for a non-Admin"

    modal = MainModal({"name": "SRS-1", "port": 10308}, admin=False)
    assert modal.port is None, "the port field is not offered to a non-Admin"
    assert not any(getattr(item, "text", None) == "Server Port" for item in modal.children), \
        "no port field is added to the modal"


@pytest.mark.asyncio
async def test_non_admin_modal_submit_leaves_port_untouched():
    """With the port field absent, ``MainModal.on_submit`` must not clobber ``config['port']``."""
    from plugins.scheduler.views import MainModal

    config = {"name": "SRS-1", "port": 10308, "maxPlayers": 16}
    modal = MainModal(config, admin=False)
    interaction = Interaction(Bot())

    await modal.on_submit(interaction)

    assert config["port"] == 10308, "the port is left as it was"
    assert config["maxPlayers"] == 16, "the rest of the modal still submits"


@pytest.mark.asyncio
async def test_non_admin_save_refuses_a_changed_port_and_writes_nothing():
    """A non-Admin's Save with a changed port is refused with the declaration and writes nothing."""
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308, "maxPlayers": 16})
    interaction = Interaction(bot)
    view = ServerConfigView(bot, server, admin=False)
    view.config["port"] = 10400
    view.config["maxPlayers"] = 24

    await ServerConfigView.on_save(view, interaction, types.SimpleNamespace())

    assert interaction.followup.messages == [MANAGER_DENIED["port"]], \
        "the answer is the declaration's own sentence"
    assert server.settings == {"name": "SRS-1", "port": 10308, "maxPlayers": 16}, \
        "the WHOLE save is refused: nothing lands on the server"
    assert server.rename_calls == [], "no rename is attempted"
    assert server.reloaded == 0, "no reload is triggered"


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_name, key", [
    ("admin_channel", "admin"),
    ("status_channel", "status"),
    ("chat_channel", "chat"),
])
async def test_non_admin_channel_callback_is_refused(callback_name, key):
    """Each channel callback refuses a non-Admin with the declaration and sets no channel."""
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308})
    interaction = Interaction(bot)
    view = ServerConfigView(bot, server, admin=False)

    await getattr(ServerConfigView, callback_name)(view, interaction, _select())

    assert interaction.followup.messages == [MANAGER_DENIED[CHANNELS_ITEM]], \
        "the answer is the declaration's own sentence"
    assert view.channel_update is False, "channel_update stays False, so the post-view write cannot fire"
    assert server.locals.get("channels") is None, "no channel is written into server.locals"


# --------------------------------------------------------- the ordinary path: an Admin still works

def test_admin_controls_are_enabled_and_modal_has_port():
    """An Admin keeps every channel select enabled and the port field in the main modal."""
    from plugins.scheduler.views import MainModal, ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308})
    view = ServerConfigView(bot, server, admin=True)

    assert [view.children[i].disabled for i in (0, 1, 2)] == [False, False, False], \
        "an Admin's channel selects are enabled"

    modal = MainModal({"name": "SRS-1", "port": 10308}, admin=True)
    assert modal.port is not None, "the port field is offered to an Admin"
    assert any(getattr(item, "text", None) == "Server Port" for item in modal.children), \
        "the port field is added to the modal"


@pytest.mark.asyncio
async def test_admin_save_writes_a_changed_port():
    """An Admin's Save still writes the changed port — the guard does not break the primary caller."""
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308, "maxPlayers": 16})
    interaction = Interaction(bot)
    view = ServerConfigView(bot, server, admin=True)
    view.config["port"] = 10400

    await ServerConfigView.on_save(view, interaction, types.SimpleNamespace())

    assert server.settings["port"] == 10400, "the Admin's port change landed"
    assert interaction.followup.messages == [], "no refusal is sent to an Admin"


@pytest.mark.asyncio
async def test_admin_channel_callback_writes():
    """An Admin's channel pick still writes the channel and raises ``channel_update``."""
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    server = ConfigServer(settings={"name": "SRS-1", "port": 10308})
    interaction = Interaction(bot)
    view = ServerConfigView(bot, server, admin=True)

    await ServerConfigView.admin_channel(view, interaction, _select(777))

    assert view.channel_update is True, "channel_update is raised for an Admin"
    assert server.locals["channels"]["admin"] == 777, "the picked channel is written"
    assert interaction.followup.messages == [], "no refusal is sent to an Admin"
