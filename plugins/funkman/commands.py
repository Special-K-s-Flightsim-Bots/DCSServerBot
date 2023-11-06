import os

from configparser import ConfigParser
from core import Plugin, PluginInstallationError, PluginConfigurationError, DEFAULT_TAG
from services import DCSServerBot
from .listener import FunkManEventListener

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class FunkMan(Plugin):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            raise PluginInstallationError('funkman', "Can't find config/plugins/funkman.yaml, please create one!")
        return config

    async def install(self) -> bool:
        if await super().install():
            config = self.get_config()
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
                with open('config/plugins/funkman.yaml', 'w') as outfile:
                    yaml.dump({DEFAULT_TAG: config}, outfile)
            return True
        return False

async def setup(bot: DCSServerBot):
    await bot.add_cog(FunkMan(bot, FunkManEventListener))
