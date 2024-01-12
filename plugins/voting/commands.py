from core import Plugin
from plugins.voting.listener import VotingListener
from services import DCSServerBot


class Voting(Plugin):
    pass


async def setup(bot: DCSServerBot):
    await bot.add_cog(Voting(bot, VotingListener))
