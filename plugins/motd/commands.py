from core import DCSServerBot, Plugin, PluginRequiredError
from .listener import MessageOfTheDayListener


class MessageOfTheDay(Plugin):
    pass


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(MessageOfTheDay(bot, MessageOfTheDayListener))
