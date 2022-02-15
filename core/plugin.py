import json
import os
import psycopg2
import psycopg2.extras
import string
from core import utils
from contextlib import closing
from discord.ext import commands
from os import path
from shutil import copytree
from typing import Type
from .bot import DCSServerBot
from .listener import TEventListener


class Plugin(commands.Cog):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        self.plugin = type(self).__module__.split('.')[-2]
        self.plugin_version = None
        self.bot = bot
        self.log = bot.log
        self.config = bot.config
        self.pool = bot.pool
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
        self.log.debug(f'- Plugin {type(self).__name__} unloaded.')

    def install(self):
        self.init_db()
        for installation in utils.findDCSInstallations():
            if installation not in self.config:
                continue
            source_path = f'./plugins/{self.plugin}/lua'
            if path.exists(source_path):
                target_path = path.expandvars(self.config[installation]['DCS_HOME'] + f'\\Scripts\\net\\DCSServerBot\\{self.plugin}\\')
                copytree(source_path, target_path, dirs_exist_ok=True)
                self.log.debug(f'  => Luas installed into {installation}')
        # create report directories for convenience
        source_path = f'./plugins/{self.plugin}/reports'
        if path.exists(source_path):
            target_path = f'./reports/{self.plugin}'
            if not path.exists(target_path):
                os.makedirs(target_path)

    def init_db(self):
        tables_file = f'./plugins/{self.plugin}/db/tables.sql'
        if path.exists(tables_file):
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin, ))
                    row = cursor.fetchone()
                    if row:
                        self.plugin_version = row[0]
                        updates_file = f'./plugins/{self.plugin}/db/update_{self.plugin_version}.sql'
                        while path.exists(updates_file):
                            with open(updates_file) as updates_sql:
                                for query in updates_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                            cursor.execute('SELECT version FROM plugins WHERE plugin = %s', (self.plugin,))
                            self.plugin_version = cursor.fetchone()[0]
                            self.log.info(f'  => {string.capwords(self.plugin)} updated to version {self.plugin_version}.')
                            updates_file = f'./plugins/{self.plugin}/db/update_{self.plugin_version}.sql'
                    else:
                        with open(tables_file) as tables_sql:
                            for query in tables_sql.readlines():
                                self.log.debug(query.rstrip())
                                cursor.execute(query.rstrip())
                        self.log.info(f'  => {string.capwords(self.plugin)} installed.')
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    def read_locals(self):
        filename = f'./config/{self.plugin}.json'
        if path.exists(filename):
            self.log.debug(f'  => Reading plugin configuration from {filename} ...')
            with open(filename) as file:
                return json.load(file)
        else:
            return {}


class PluginRequiredError(Exception):

    def __init__(self, plugin):
        super().__init__(f'Required plugin "{string.capwords(plugin)}" is missing!')
