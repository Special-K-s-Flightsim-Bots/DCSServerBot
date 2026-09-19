from core import Plugin
from services.bot import DCSServerBot

from .listener import DebugListener


class Debug(Plugin[DebugListener]):
    ...


async def setup(bot: DCSServerBot):
    plugin = Debug(bot, DebugListener)
    plugin.log.warning(f"The {plugin.__cog_name__} plugin is activated. This can result in performance degradation.")
    await bot.add_cog(plugin)
