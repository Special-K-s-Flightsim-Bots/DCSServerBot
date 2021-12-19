from .bot import DCSServerBot
from .listener import EventListener
from discord.ext import commands


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, listener: EventListener = None):
        self.bot = bot
        self.log = bot.log
        self.config = bot.config
        self.pool = bot.pool
        self.listener = listener
        if self.listener:
            self.bot.register_eventListener(self.listener)
        self.log.debug(f'- Plugin {type(self).__name__} initialized.')

    def cog_unload(self):
        self.__unload__()
        self.log.debug(f'- Plugin {type(self).__name__} unloaded.')

    def __unload__(self):
        if self.listener:
            self.bot.unregister_eventListener(self.listener)


class PluginRequiredError(Exception):

    def __init__(self, plugin):
        super().__init__(f'Required plugin "{plugin}" is missing!')
