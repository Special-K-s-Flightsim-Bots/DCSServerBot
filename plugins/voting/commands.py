from typing import Type

from core import Plugin, TEventListener, PluginInstallationError
from plugins.voting.listener import VotingListener
from services.bot import DCSServerBot


class Voting(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener=eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Voting(bot, VotingListener))
