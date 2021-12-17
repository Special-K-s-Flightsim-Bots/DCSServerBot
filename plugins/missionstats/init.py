from .commands import *
from .listener import *


def setup(bot):
    bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener(bot)))
