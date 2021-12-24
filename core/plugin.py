from .bot import DCSServerBot
from .listener import EventListener
from discord.ext import commands


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, eventlistener: EventListener = None):
        self.bot = bot
        self.log = bot.log
        self.config = bot.config
        self.pool = bot.pool
        self.eventlistener = eventlistener
        if self.eventlistener:
            self.bot.register_eventListener(self.eventlistener)
        self.log.debug(f'- Plugin {type(self).__name__} initialized.')

    def cog_unload(self):
        if self.eventlistener:
            self.bot.unregister_eventListener(self.eventlistener)
        self.log.debug(f'- Plugin {type(self).__name__} unloaded.')


class PluginRequiredError(Exception):

    def __init__(self, plugin):
        super().__init__(f'Required plugin "{plugin}" is missing!')
