from core import DCSServerBot, Plugin
from .listener import WeaponBlockingListener


class WeaponBlocking(Plugin):
    """
    A class where all your discord commands should go.

    If you need a specific initialization, make sure that you call super().__init__() after it, to
    assure a proper initialization of the plugin.

    Attributes
    ----------
    bot: DCSServerBot
        The discord bot instance.
    listener: EventListener
        A listener class to receive events from DCS.

    Methods
    -------
    sample(ctx, text)
        Send the text to DCS, which will return the same text again (echo).
    """
async def setup(bot: DCSServerBot):
    await bot.add_cog(WeaponBlocking(bot, WeaponBlockingListener))
