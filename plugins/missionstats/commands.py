from core import DCSServerBot, Plugin
from .listener import MissionStatisticsEventListener


class MissionStatistics(Plugin):
    pass


def setup(bot: DCSServerBot):
    bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener(bot)))
