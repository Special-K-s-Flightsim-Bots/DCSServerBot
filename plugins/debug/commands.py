import discord

from core import Plugin, command, utils
from discord import app_commands
from services.bot import DCSServerBot


class Debug(Plugin):
    ...


async def setup(bot: DCSServerBot):
    await bot.add_cog(Debug(bot))
