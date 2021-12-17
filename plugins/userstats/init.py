from .commands import *
from .listener import *
from core import PluginRequiredError


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    listener = UserStatisticsEventListener(bot)
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(MasterUserStatistics(bot, listener))
    else:
        bot.add_cog(AgentUserStatistics(bot, listener))
        