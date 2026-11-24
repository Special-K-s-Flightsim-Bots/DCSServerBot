"""A ``no_discord`` install can reload a plugin — the apply door's own precondition.

The console's apply door asks the bot three things before it reloads anything: whether it can reload
at all, whether the plugin is loaded, and then it unloads and loads it. The headless bot answered none
of them, so every apply on such an install ended in "the bot process cannot be reached from here".
"""
from __future__ import annotations

import asyncio


def _bare_bot(cogs):
    from services.bot.dummy.bot import DummyBot

    bot = object.__new__(DummyBot)
    bot.cogs = dict(cogs)
    return bot


class FakeCog:
    def __init__(self):
        self.unloaded = False

    async def cog_unload(self) -> None:
        self.unloaded = True


def test_the_cogs_are_named_the_way_the_apply_door_asks_for_them():
    bot = _bare_bot({"Scheduler": FakeCog()})
    assert list(bot.extensions) == ["plugins.scheduler.commands"]


def test_unload_takes_the_cog_out_and_runs_its_own_unload():
    cog = FakeCog()
    bot = _bare_bot({"Scheduler": cog})
    assert asyncio.run(bot.unload_plugin("scheduler")) is True
    assert bot.cogs == {}
    assert cog.unloaded


def test_unloading_something_that_is_not_loaded_is_false_not_an_error():
    assert asyncio.run(_bare_bot({}).unload_plugin("scheduler")) is False


def test_the_headless_bot_offers_a_reload_the_preflight_accepts():
    from services.webservice import config_apply

    bot = _bare_bot({"Scheduler": FakeCog()})
    assert callable(getattr(bot, "reload_plugin", None))
    assert "plugins.scheduler.commands" in config_apply._loaded_extensions(bot)
