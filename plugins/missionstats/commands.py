from core import DCSServerBot, Plugin, PluginRequiredError
from .listener import MissionStatisticsEventListener


class MissionStatistics(Plugin):
    pass


def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener(bot)))
