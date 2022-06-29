import shutil
from core import Plugin, DCSServerBot, PluginRequiredError
from os import path
from .listener import AirBossEventListener


class AirBoss(Plugin):
    pass


def setup(bot: DCSServerBot):
    if 'greenieboard' not in bot.plugins:
        raise PluginRequiredError('greenieboard')
    # make sure that we have a proper configuration, take the default one if none is there
    if not path.exists('config/airboss.json'):
        bot.log.info('No airboss.json found, copying the sample.')
        shutil.copyfile('config/airboss.json.sample', 'config/airboss.json')
    bot.add_cog(AirBoss(bot, AirBossEventListener))
