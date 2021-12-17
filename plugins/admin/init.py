from .commands import *
from .listener import *


def setup(bot):
    listener = AdminEventListener(bot)
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Master(bot, listener))
    else:
        bot.add_cog(Agent(bot, listener))
