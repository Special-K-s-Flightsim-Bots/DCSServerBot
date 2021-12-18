from .commands import *


def setup(bot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Help(bot))
