from core import Plugin, command, utils
from services.bot import DCSServerBot


class Debug(Plugin):
    ...


async def setup(bot: DCSServerBot):
    plugin = Debug(bot)
    plugin.log.warning(f"The {plugin.__cog_name__} plugin is activated. This can result in performance degradation.")
    await bot.add_cog(plugin)
