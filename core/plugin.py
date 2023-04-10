from __future__ import annotations
import json
import os
import psycopg2
import psycopg2.extras
import sys
from contextlib import closing
from copy import deepcopy
from core import utils
from discord.ext import commands
from os import path
from shutil import copytree
from typing import Type, Optional, TYPE_CHECKING
from .listener import TEventListener

if TYPE_CHECKING:
    from core import DCSServerBot, Server


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__()
        self.plugin_name = type(self).__module__.split('.')[-2]
        self.plugin_version = getattr(sys.modules['plugins.' + self.plugin_name], '__version__')
        self.bot: DCSServerBot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.loop = bot.loop
        self.locals = self.read_locals()
        if self.plugin_name != 'commands' and 'commands' in self.locals:
            self.change_commands(self.locals['commands'])
        self._config = dict[str, dict]()
        self.eventlistener: Type[TEventListener] = eventlistener(self) if eventlistener else None

    async def cog_load(self) -> None:
        await self.install()
        if self.eventlistener:
            self.bot.register_eventListener(self.eventlistener)
        self.log.info(f'  => {self.plugin_name.title()} loaded.')

    async def cog_unload(self):
        if self.eventlistener:
            await self.eventlistener.shutdown()
            self.bot.unregister_eventListener(self.eventlistener)
        # delete a possible configuration
        self._config.clear()
        self.log.info(f'  => {self.plugin_name.title()} unloaded.')

    def change_commands(self, cmds: dict) -> None:
        all_cmds = {x.name: x for x in self.get_commands()}
        for name, params in cmds.items():
            cmd: commands.Command = all_cmds.get(name)
            if not cmd:
                self.log.warning(f"{self.plugin_name}: {name} is not a command!")
                continue
            if 'roles' in params:
                for idx, check in enumerate(cmd.checks.copy()):
                    if 'has_role' in check.__qualname__:
                        cmd.checks.pop(idx)
                if len(params['roles']):
                    cmd.checks.append(utils.has_roles(params['roles'].copy()).predicate)
                del params['roles']
            if params:
                cmd.update(**params)

    @staticmethod
    def get_installed_version(plugin: str) -> Optional[str]:
        file = 'config/.plugins.json'
        if not os.path.exists(file):
            return None
        with open(file) as f:
            installed = json.load(f)
        return installed[plugin] if plugin in installed else None

    @staticmethod
    def set_installed_version(plugin: str, version: str):
        file = 'config/.plugins.json'
        if not os.path.exists(file):
            installed = {}
        else:
            with open(file) as f:
                installed = json.load(f)
        installed[plugin] = version
        with open(file, 'w') as f:
            json.dump(installed, f, indent=2)

    async def install(self):
        # don't init the DB on agents, whole DB handling is a master task
        if self.bot.config.getboolean('BOT', 'MASTER') is True:
            self.init_db()
        else:
            version = self.get_installed_version(self.plugin_name)
            if not version:
                self.set_installed_version(self.plugin_name, self.plugin_version)
            elif version != self.plugin_version:
                self.migrate(self.plugin_version)
                self.set_installed_version(self.plugin_name, self.plugin_version)
        for server in self.bot.servers.values():
            source_path = f'./plugins/{self.plugin_name}/lua'
            if path.exists(source_path):
                target_path = path.expandvars(self.bot.config[server.installation]['DCS_HOME'] +
                                              f'\\Scripts\\net\\DCSServerBot\\{self.plugin_name}\\')
                copytree(source_path, target_path, dirs_exist_ok=True)
                self.log.debug(f'  => Luas installed into {server.installation}')
        # create report directories for convenience
        source_path = f'./plugins/{self.plugin_name}/reports'
        if path.exists(source_path):
            target_path = f'./reports/{self.plugin_name}'
            if not path.exists(target_path):
                os.makedirs(target_path)

    def migrate(self, version: str) -> None:
        pass

    async def before_dcs_update(self) -> None:
        pass

    async def after_dcs_update(self) -> None:
        pass

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None) -> None:
        pass

    def init_db(self) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin_name,))
                # first installation
                if cursor.rowcount == 0:
                    tables_file = f'./plugins/{self.plugin_name}/db/tables.sql'
                    if path.exists(tables_file):
                        with open(tables_file) as tables_sql:
                            for query in tables_sql.readlines():
                                self.log.debug(query.rstrip())
                                cursor.execute(query.rstrip())
                    cursor.execute('INSERT INTO plugins (plugin, version) VALUES (%s, %s) ON CONFLICT (plugin) DO '
                                   'NOTHING', (self.plugin_name, self.plugin_version))
                    self.log.info(f'  => {self.plugin_name.title()} installed.')
                else:
                    installed = cursor.fetchone()[0]
                    # old variant, to be migrated
                    if installed.startswith('v'):
                        installed = installed[1:]
                    while installed != self.plugin_version:
                        updates_file = f'./plugins/{self.plugin_name}/db/update_v{installed}.sql'
                        if path.exists(updates_file):
                            with open(updates_file) as updates_sql:
                                for query in updates_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                        ver, rev = installed.split('.')
                        installed = ver + '.' + str(int(rev) + 1)
                        self.migrate(installed)
                        self.log.info(f'  => {self.plugin_name.title()} migrated to version {installed}.')
                    cursor.execute('UPDATE plugins SET version = %s WHERE plugin = %s',
                                   (self.plugin_version, self.plugin_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def read_locals(self) -> dict:
        if path.exists(f'./config/{self.plugin_name}.json'):
            filename = f'./config/{self.plugin_name}.json'
        elif path.exists(f'./plugins/{self.plugin_name}/config/config.json'):
            filename = f'./plugins/{self.plugin_name}/config/config.json'
        else:
            return {}
        self.log.debug(f'  => Reading plugin configuration from {filename} ...')
        with open(filename, encoding='utf-8') as file:
            return json.load(file)

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    self._config[server.name] = default | specific
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    def rename(self, old_name: str, new_name: str) -> None:
        # this function has to be implemented in your own plugins, if a server rename takes place
        pass


class PluginError(Exception):
    pass


class PluginRequiredError(PluginError):
    def __init__(self, plugin: str):
        super().__init__(f'Required plugin "{plugin.title()}" is missing!')


class PluginConflictError(PluginError):
    def __init__(self, plugin1: str, plugin2: str):
        super().__init__(f'Plugin "{plugin1.title()}" conflicts with plugin "{plugin2.title()}"!')


class PluginConfigurationError(PluginError):
    def __init__(self, plugin: str, option: str):
        super().__init__(f'Option "{option}" missing in {plugin}.json!')


class PluginInstallationError(PluginError):
    def __init__(self, plugin: str, reason: str):
        super().__init__(f'Plugin "{plugin.title()}" could not be installed: {reason}')
