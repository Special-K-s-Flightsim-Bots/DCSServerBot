import os
import psycopg

from configparser import ConfigParser
from core import Plugin, PluginInstallationError, PluginConfigurationError, DEFAULT_TAG, Server
from services.bot import DCSServerBot
from typing import Optional

from .listener import FunkManEventListener

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML(typ='safe')


class FunkMan(Plugin[FunkManEventListener]):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            raise PluginInstallationError(self.plugin_name,
                                          f"Can't find {self.node.config_dir}/plugins/funkman.yaml, "
                                          f"please create one!")
        path = config.get(DEFAULT_TAG, {}).get('install')
        if not path or not os.path.exists(path):
            raise PluginInstallationError(self.plugin_name,
                                          f"FunkMan install path is not set correctly in the DEFAULT-section of "
                                          f"your {self.plugin_name}.yaml! FunkMan will not work.")
        return config

    async def install(self) -> bool:
        if await super().install():
            config = self.get_config()
            if 'install' not in config:
                raise PluginConfigurationError(self.plugin_name, 'install')
            funkpath = os.path.expandvars(config['install'])
            if not os.path.exists(funkpath) or not os.path.exists(os.path.join(funkpath, 'FunkMan.ini')):
                self.log.error(f"No FunkMan installation found at {funkpath}!")
                raise PluginConfigurationError(self.plugin_name, 'install')
            if 'CHANNELID_MAIN' not in config:
                self.log.info('  => Migrating FunkMan.ini ...')
                ini = ConfigParser()
                ini.read(os.path.join(config['install'], 'FunkMan.ini'))
                if 'CHANNELID_MAIN' in ini['FUNKBOT']:
                    config['CHANNELID_MAIN'] = int(ini['FUNKBOT']['CHANNELID_MAIN'])
                if 'CHANNELID_RANGE' in ini['FUNKBOT']:
                    config['CHANNELID_RANGE'] = int(ini['FUNKBOT']['CHANNELID_RANGE'])
                if 'CHANNELID_AIRBOSS' in ini['FUNKBOT']:
                    config['CHANNELID_AIRBOSS'] = int(ini['FUNKBOT']['CHANNELID_AIRBOSS'])
                if 'IMAGEPATH' in ini['FUNKPLOT']:
                    if ini['FUNKPLOT']['IMAGEPATH'].startswith('.'):
                        config['IMAGEPATH'] = config['install'] + ini['FUNKPLOT']['IMAGEPATH'][1:]
                    else:
                        config['IMAGEPATH'] = ini['FUNKPLOT']['IMAGEPATH']
                with open(os.path.join(self.node.config_dir, 'plugins', 'funkman.yaml'), mode='w',
                          encoding='utf-8') as outfile:
                    yaml.dump({DEFAULT_TAG: config}, outfile)
            return True
        return False

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        # retrieve the config from another plugin
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name] or not use_cache:
            default, specific = self.get_base_config(server)
            for x in ['strafe_board', 'strafe_channel', 'bomb_board', 'bomb_channel']:
                default.pop(x, None)
            self._config[server.node.name][server.instance.name] = default | specific
        return self._config[server.node.name][server.instance.name]

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning FunkMan ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM bomb_runs WHERE player_ucid = %s', (ucid,))
                await conn.execute('DELETE FROM strafe_runs WHERE player_ucid = %s', (ucid,))
        elif days > -1:
            await conn.execute(f"""
                DELETE FROM bomb_runs WHERE time < (DATE(now() AT TIME ZONE 'utc') - %s::interval)
            """, (f'{days} days', ))
            await conn.execute("""
                DELETE FROM strafe_runs WHERE time < (DATE(now() AT TIME ZONE 'utc') - %s::interval)
            """, (f'{days} days', ))
        self.log.debug('FunkMan pruned.')


async def setup(bot: DCSServerBot):
    await bot.add_cog(FunkMan(bot, FunkManEventListener))
