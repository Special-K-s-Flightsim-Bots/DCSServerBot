import asyncio
import json
import os
import psycopg2
import psycopg2.extras
import string
from contextlib import closing
from discord.ext import commands
from os import path
from shutil import copytree
from typing import Type, Optional
from .bot import DCSServerBot
from .listener import TEventListener


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        self.plugin_name = type(self).__module__.split('.')[-2]
        self.plugin_version = None
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
        self.log.debug(f'- Plugin {type(self).__name__} initialized.')

    def cog_unload(self):
        if self.eventlistener:
            self.bot.unregister_eventListener(self.eventlistener)
        # delete a possible configuration
        for server in self.bot.globals.values():
            del server[self.plugin_name]
        self.log.debug(f'- Plugin {type(self).__name__} unloaded.')

    def install(self):
        # don't init the DB on agents, whole DB handling is a master task
        if self.config.getboolean('BOT', 'MASTER') is True:
            self.init_db()
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

    def init_db(self):
        tables_file = f'./plugins/{self.plugin_name}/db/tables.sql'
        if path.exists(tables_file):
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin_name,))
                    row = cursor.fetchone()
                    if row:
                        self.plugin_version = row[0]
                        updates_file = f'./plugins/{self.plugin_name}/db/update_{self.plugin_version}.sql'
                        while path.exists(updates_file):
                            with open(updates_file) as updates_sql:
                                for query in updates_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                            cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin_name,))
                            old_version = self.plugin_version
                            self.plugin_version = cursor.fetchone()[0]
                            self.log.info(f'  => {string.capwords(self.plugin_name)} migrated to version {self.plugin_version}.')
                            updates_file = f'./plugins/{self.plugin_name}/db/update_{self.plugin_version}.sql'
                            self.migrate(self.plugin_version)
                    else:
                        with open(tables_file) as tables_sql:
                            for query in tables_sql.readlines():
                                self.log.debug(query.rstrip())
                                cursor.execute(query.rstrip())
                        self.log.info(f'  => {string.capwords(self.plugin_name)} installed.')
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    def read_locals(self):
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
                            specific = element.copy()
                    else:
                        default = element.copy()
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

    def __init__(self, plugin):
        super().__init__(f'Required plugin "{string.capwords(plugin)}" is missing!')
