import json
import os
from configparser import ConfigParser
from core import Plugin, DCSServerBot, PluginInstallationError, PluginConfigurationError
from .listener import FunkManEventListener


class FunkMan(Plugin):

    async def install(self):
        config = self.locals['configs'][0]
        if 'install' not in config:
            raise PluginConfigurationError(self.plugin_name, 'install')
        funkpath = os.path.expandvars(config['install'])
        if not os.path.exists(funkpath) or not os.path.exists(funkpath + os.path.sep + 'FunkMan.ini'):
            self.log.error(f"No FunkMan installation found at {funkpath}!")
            raise PluginConfigurationError(self.plugin_name, 'install')
        if 'CHANNELID_MAIN' not in config:
            self.log.info('  => Migrating FunkMan.ini ...')
            ini = ConfigParser()
            ini.read(config['install'] + os.path.sep + 'FunkMan.ini')
            if 'CHANNELID_MAIN' in ini['FUNKBOT']:
                config['CHANNELID_MAIN'] = ini['FUNKBOT']['CHANNELID_MAIN']
            if 'CHANNELID_RANGE' in ini['FUNKBOT']:
                config['CHANNELID_RANGE'] = ini['FUNKBOT']['CHANNELID_RANGE']
            if 'CHANNELID_AIRBOSS' in ini['FUNKBOT']:
                config['CHANNELID_AIRBOSS'] = ini['FUNKBOT']['CHANNELID_AIRBOSS']
            if 'IMAGEPATH' in ini['FUNKPLOT']:
                if ini['FUNKPLOT']['IMAGEPATH'].startswith('.'):
                    config['IMAGEPATH'] = config['install'] + ini['FUNKPLOT']['IMAGEPATH'][1:]
                else:
                    config['IMAGEPATH'] = ini['FUNKPLOT']['IMAGEPATH']
            with open('config/funkman.json', 'w') as outfile:
                json.dump(self.locals, outfile, indent=2)
        await super().install()


async def setup(bot: DCSServerBot):
    if not os.path.exists('config/funkman.json'):
        raise PluginInstallationError('funkman', "Can't find config/funkman.json, please create one!")
    await bot.add_cog(FunkMan(bot, FunkManEventListener))
