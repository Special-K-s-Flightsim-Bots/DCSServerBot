"""A rename refusal must reach the user as a sentence, not a generic interaction failure.

``/server rename`` (``plugins/scheduler/commands.py``) and the config view's Save
(``plugins/scheduler/views.py``) both called ``Server.rename`` with no catch, so the typed refusal
(``RenameError``) surfaced as an unhandled interaction error. Both real callbacks are driven with
doubles: the refusal's own sentence is what Discord is handed, and nothing is written."""
from __future__ import annotations

import types
from typing import Any

import pytest

from core import Status
from core.data.impl.serverimpl import RenameError

REFUSAL = 'Server name "SRS-2" is already used by server "SRS-2".'


# -------------------------------------------------------------------------------------- doubles

class RenameServer:
    """The slice of a Server the two callbacks read: name, status, settings and ``rename``.

    ``refuse`` makes ``rename`` raise the typed refusal AND leave the name unmoved — the shape of a
    REFUSED rename, so the test can prove that the answer is the refusal's own sentence.
    """

    def __init__(self, name, *, status=Status.SHUTDOWN, settings=None, refuse=None):
        self.name = name
        self.display_name = name
        self.status = status
        self.settings = dict(settings or {})
        self.locals = {}
        self._refuse = refuse
        self.rename_calls: list[tuple] = []

    async def rename(self, new_name, update_settings=False):
        self.rename_calls.append((new_name, update_settings))
        if self._refuse is not None:
            raise RenameError(self._refuse)
        self.name = new_name

    async def reload(self):
        pass


class Response:
    def __init__(self):
        self.deferrals: list[dict] = []
        self.messages: list = []

    async def defer(self, *, thinking=False, ephemeral=False):
        self.deferrals.append({"thinking": thinking, "ephemeral": ephemeral})

    async def send_message(self, content=None, **kwargs):
        self.messages.append(content)


class Followup:
    def __init__(self):
        self.messages: list = []

    async def send(self, content=None, **kwargs):
        self.messages.append(content)


class Interaction:
    """A Discord interaction double: the response the callbacks defer on, and the followup they use."""

    def __init__(self, bot):
        self.client = bot
        self.response = Response()
        self.followup = Followup()
        self.user = types.SimpleNamespace(display_name="member")
        self.channel = None


class Bot:
    """The slice of the bot the callbacks read: the registry, the audit trail, ``get_server``."""

    def __init__(self):
        self.servers: dict = {}
        self.audited: list[tuple] = []
        self.locals: dict = {}

    def get_server(self, interaction):
        return None                      # → ``utils.get_ephemeral`` returns True (no server here)

    def get_admin_channel(self, server):
        return None

    async def audit(self, event, server=None, **kwargs):
        self.audited.append((event, getattr(server, "name", None)))


# --------------------------------------------------------------------------- the two real callbacks

@pytest.fixture
def scheduler_commands(monkeypatch):
    """Import ``plugins.scheduler.commands`` with the ``BotService`` lookup stubbed.

    The module builds its app-commands at class-body time (``core.plugin.Command`` reads
    ``ServiceRegistry.get(BotService).bot``, a cluster a bare test interpreter has none of); the
    lookup is stubbed just for the import, then restored.
    """
    import importlib

    from core.services.registry import ServiceRegistry
    from services.bot import BotService

    stub_bot = types.SimpleNamespace(node=types.SimpleNamespace(all_nodes={"n": 1}),
                                     servers={}, locals={})
    saved = ServiceRegistry._singletons.get(BotService, None)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=stub_bot)
    try:
        yield importlib.import_module("plugins.scheduler.commands")
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


@pytest.mark.asyncio
async def test_server_rename_answers_with_the_refusal_sentence(scheduler_commands):
    """``/server rename`` (``commands.py``) answers with the refusal's own sentence.

    The call is caught, the followup carries the sentence, no audit entry is written and the server is
    not renamed.
    """
    callback: Any = scheduler_commands.Scheduler._rename.callback
    bot = Bot()
    interaction = Interaction(bot)
    server = RenameServer("SRS-1", settings={"name": "SRS-1"}, refuse=REFUSAL)

    await callback(types.SimpleNamespace(bot=bot), interaction, server, "SRS-2")

    assert interaction.followup.messages == [REFUSAL], "the refusal's own sentence is what the user is handed"
    assert server.rename_calls == [("SRS-2", True)], "the rename was attempted"
    assert server.name == "SRS-1", "nothing was renamed"
    assert bot.audited == [], "no success audit is written for a refusal"
    assert bot.servers == {}, "the registry is untouched"


@pytest.mark.asyncio
async def test_config_save_answers_with_the_refusal_sentence():
    """The config view's Save (``views.py``) answers with the refusal's own sentence.

    It returns BEFORE the settings write and the manual re-key, so nothing is written.
    """
    from plugins.scheduler.views import ServerConfigView

    bot = Bot()
    interaction = Interaction(bot)
    server = RenameServer("SRS-1", settings={"name": "SRS-1", "maxPlayers": 16}, refuse=REFUSAL)
    view = types.SimpleNamespace(server=server, config={"name": "SRS-2", "maxPlayers": 24})

    await ServerConfigView.on_save(view, interaction, types.SimpleNamespace())

    assert interaction.followup.messages == [REFUSAL], "the refusal's own sentence is what the user is handed"
    assert server.rename_calls == [("SRS-2", True)], "the rename was attempted"
    assert server.name == "SRS-1", "nothing was renamed"
    assert server.settings == {"name": "SRS-1", "maxPlayers": 16}, "the settings loop was never reached"
    assert bot.servers == {}, "the manual re-key was never reached"
