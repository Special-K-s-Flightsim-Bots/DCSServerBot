from core import Plugin
from plugins.firewall.listener import FirewallListener
from services import DCSServerBot


class Firewall(Plugin):
    ...


async def setup(bot: DCSServerBot):
    await bot.add_cog(Firewall(bot, FirewallListener))
