import asyncio
import json
import os
import psycopg2
import psycopg2.extras
import string
import sys
from contextlib import closing
from copy import deepcopy
from discord.ext import commands
from os import path
from shutil import copytree
from typing import Type, Optional
from .bot import DCSServerBot
from .listener import TEventListener


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        self.plugin_name = type(self).__module__.split('.')[-2]
        self.plugin_version = getattr(sys.modules['plugins.' + self.plugin_name], '__version__')
        self.bot = bot
        self.log = bot.log
        self.config = bot.config
        self.pool = bot.pool
        self.loop = asyncio.get_event_loop()
        self.globals = bot.globals
        self.locals = self.read_locals()
        self.eventlistener = eventlistener(self) if eventlistener else None
        self.install()
        if self.eventlistener:
            self.bot.register_eventListener(self.eventlistener)
        self.log.debug(f'- Plugin {type(self).__name__} v{self.plugin_version} initialized.')

    def cog_unload(self):
        if self.eventlistener:
            self.bot.unregister_eventListener(self.eventlistener)
        # delete a possible configuration
        for server in self.bot.globals.values():
            del server[self.plugin_name]
        self.log.debug(f'- Plugin {type(self).__name__} unloaded.')

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

    def install(self):
        # don't init the DB on agents, whole DB handling is a master task
        if self.config.getboolean('BOT', 'MASTER') is True:
            self.init_db()
        else:
            version = self.get_installed_version(self.plugin_name)
            if not version:
                self.set_installed_version(self.plugin_name, self.plugin_version)
            elif version != self.plugin_version:
                self.migrate(self.plugin_version)
                self.set_installed_version(self.plugin_name, self.plugin_version)
        for server in self.globals.values():
            installation = server['installation']
            source_path = f'./plugins/{self.plugin_name}/lua'
            if path.exists(source_path):
                target_path = path.expandvars(self.config[installation]['DCS_HOME'] +
                                              f'\\Scripts\\net\\DCSServerBot\\{self.plugin_name}\\')
                copytree(source_path, target_path, dirs_exist_ok=True)
                self.log.debug(f'  => Luas installed into {installation}')
        # create report directories for convenience
        source_path = f'./plugins/{self.plugin_name}/reports'
        if path.exists(source_path):
            target_path = f'./reports/{self.plugin_name}'
            if not path.exists(target_path):
                os.makedirs(target_path)

    def migrate(self, version: str):
        pass

    async def before_dcs_update(self):
        pass

    async def after_dcs_update(self):
        pass

    def init_db(self):
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
                    self.log.info(f'  => {string.capwords(self.plugin_name)} installed.')
                else:
                    installed = cursor.fetchone()[0]
                    updates_file = f'./plugins/{self.plugin_name}/db/update_v{installed}.sql'
                    if not path.exists(updates_file) and not installed.startswith('v'):
                        return
                    while path.exists(updates_file):
                        with open(updates_file) as updates_sql:
                            for query in updates_sql.readlines():
                                self.log.debug(query.rstrip())
                                cursor.execute(query.rstrip())
                        ver, rev = installed.split('.')
                        installed = ver + '.' + str(int(rev) + 1)
                        self.log.info(f'  => {string.capwords(self.plugin_name)} migrated to version {installed}.')
                        updates_file = f'./plugins/{self.plugin_name}/db/update_v{installed}.sql'
                        self.migrate(installed)
                    cursor.execute('UPDATE plugins SET version = %s WHERE plugin = %s',
                                   (self.plugin_version, self.plugin_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def read_locals(self) -> dict:
        filename = f'./config/{self.plugin_name}.json'
        if path.exists(filename):
            self.log.debug(f'  => Reading plugin configuration from {filename} ...')
            with open(filename) as file:
                return json.load(file)
        else:
            return {}

    def get_config(self, server: dict) -> Optional[dict]:
        if self.plugin_name not in server:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server['installation'] == element['installation']) or \
                                ('server_name' in element and server['server_name'] == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    server[self.plugin_name] = default
                elif specific and not default:
                    server[self.plugin_name] = specific
                elif default and specific:
                    server[self.plugin_name] = default | specific
            else:
                return None
        return server[self.plugin_name] if self.plugin_name in server else None

    def rename(self, old_name:str, new_name: str):
        # this function has to be implemented in your own plugins, if a server rename takes place
        pass


class PluginRequiredError(Exception):
    def __init__(self, plugin: str):
        super().__init__(f'Required plugin "{string.capwords(plugin)}" is missing!')


class PluginConflictError(Exception):
    def __init__(self, plugin1: str, plugin2: str):
        super().__init__(f'Plugin "{string.capwords(plugin1)}" conflicts with plugin "{string.capwords(plugin2)}"!')


class PluginConfigurationError(Exception):
    def __init__(self, plugin: str, option: str):
        super().__init__(f'Option "{option}" missing in {plugin}.json!')
