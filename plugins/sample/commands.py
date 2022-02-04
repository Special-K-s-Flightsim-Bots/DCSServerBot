from core import DCSServerBot, Plugin, utils
from discord.ext import commands
from .listener import SampleEventListener


class Sample(Plugin):
    """
    A class where all your discord commands should go.

    If you need a specific initialization, make sure that you call super().__init__() after it, to
    assure a proper initialization of the plugin.

    Attributes
    ----------
    plugin : str
        The name of this plugin. Must match the directory it is stored in.
    bot: DCSServerBot
        The discord bot instance.
    listener : EventListener
        A listener class to receive events from DCS.

    Methods
    -------
    sample(ctx, text)
        Send the text to DCS, which will return the same text again (echo).
    """

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        # Do whatever is needed to initialize your plugin.
        # You usually don't need to implement this function

    @commands.command(description='This is a sample command.')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def sample(self, ctx, text):
        # the server to run the command on will be determined from the channel where you called the command in
        server = await utils.get_server(self, ctx)
        # Calls can be done async (default) or synchronous, which means we will wait for a response from DCS
        data = self.bot.sendtoDCSSync(server, {
            "command": "sample",        # command name
            "message": text,            # the message to transfer
            "channel": ctx.channel.id   # the channel where the response should go to
        })
        await ctx.send(data['message'])


def setup(bot: DCSServerBot):
    bot.add_cog(Sample(bot, SampleEventListener))
