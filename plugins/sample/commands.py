import discord

from core import Plugin, utils, Server, Status, command
from discord import app_commands
from services.bot import DCSServerBot
from typing import Type

from .listener import SampleEventListener


class Sample(Plugin[SampleEventListener]):
    """
    A class where all your discord commands should go.

    If you need a specific initialization, make sure that you call super().__init__() after it, to
    assure a proper initialization of the plugin.

    Attributes
    ----------
    :param bot: DCSServerBot
        The discord bot instance.
    :param listener: EventListener
        A listener class to receive events from DCS.

    Methods
    -------
    sample(ctx, text)
        Send the text to DCS, which will return the same text again (echo).
    """

    def __init__(self, bot: DCSServerBot, listener: Type[SampleEventListener]):
        super().__init__(bot, listener)
        # Do whatever is needed to initialize your plugin.
        # You usually don't need to implement this function.

    @command(description='This is a sample command.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sample(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                         Status.RUNNING, Status.PAUSED, Status.STOPPED])
                     ], text: str):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True)
        # Calls can be done async (default) or synchronous, which means we will wait for a response from DCS
        data = await server.send_to_dcs_sync({
            "command": "sample",    # command name
            "message": text         # the message to transfer
        })
        await interaction.followup.send(f"Response: {data['message']}")


async def setup(bot: DCSServerBot):
    await bot.add_cog(Sample(bot, SampleEventListener))
