import discord
import logging

from core import (
    Plugin,
    utils,
    TEventListener,
    PersistentReport,
    Group,
    Server
)
from discord import app_commands
from services.bot import DCSServerBot
from typing import Type

from .listener import PypptrTestEventListener


class PypptrTest(Plugin):

    def __init__(self, bot: DCSServerBot, listener: Type[TEventListener]):
        super().__init__(bot, listener)
        # Do whatever is needed to initialize your plugin.
        # You usually don't need to implement this function.
        logging.getLogger("pyppeteer").setLevel(logging.CRITICAL)

    # New command group "/vnao"
    pypptr = Group(name="pypptr", description="Pypptr commands")

    @pypptr.command(description="Rebuild Pyppeteer board.")
    @utils.has_role("DCS Admin")
    @app_commands.guild_only()
    async def rebuild_pypptr_board(
        self,
        interaction: discord.Interaction,
        server: app_commands.Transform[Server, utils.ServerTransformer],
    ):

        self.log.debug("Rebuilding all Pyppeteer board.")

        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=True)

        config = self.get_config(server)

        report = PersistentReport(
            self.bot,
            self.plugin_name,
            "board.json",
            embed_name=f"pypptr-test",
            channel_id=config["channel_id"]
        )
        await report.render(config=config)

        await interaction.followup.send(f"Pyppeteer board has been rebuilt.")

        self.log.debug("Pyppeteer board has been rebuilt.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(PypptrTest(bot, PypptrTestEventListener))
