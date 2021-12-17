# run.py
import discord
import logging
import os
import platform
import psycopg2
import psycopg2.extras
import shutil
import subprocess
import sys
from core import utils, DCSServerBot
from configparser import ConfigParser
from contextlib import closing, suppress
from discord.ext import commands
from logging.handlers import RotatingFileHandler
from os import path
from psycopg2 import pool

# Set the bot's version (not externally configurable)
BOT_VERSION = '2.5.0'

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

# git repository
GIT_REPO_URL = 'https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git'

# Database Configuration
TABLES_SQL = 'sql/tables.sql'
UPDATES_SQL = 'sql/update_{}.sql'


class Main:

    def __init__(self):
        self.config = self.read_config()
        self.log = self.init_logger()
        self.log.info(f'DCSServerBot v{BOT_VERSION} starting up ...')
        if self.config.getboolean('BOT', 'AUTOUPDATE'):
            self.upgrade()
        self.pool = self.init_db()
        self.sanitize()
        self.install_hooks()
        self.bot = self.init_bot()
        self.add_commands()

    def init_logger(self):
        # Initialize the logger
        log = logging.getLogger(name='dcsserverbot')
        log.setLevel(logging.DEBUG)
        fh = RotatingFileHandler('dcsserverbot.log', maxBytes=10*1024*2024, backupCount=2)
        if 'LOGLEVEL' in self.config['BOT']:
            fh.setLevel(LOGLEVEL[self.config['BOT']['LOGLEVEL']])
        else:
            fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        fh.doRollover()
        log.addHandler(fh)
        ch = logging.StreamHandler()
        # TODO: Change back to INFO
        ch.setLevel(logging.INFO)
        log.addHandler(ch)
        return log

    def read_config(self):
        config = ConfigParser()
        config.read('config/default.ini')
        config.read('config/dcsserverbot.ini')
        config['BOT']['VERSION'] = BOT_VERSION
        return config

    def init_db(self):
        # Initialize the database
        db_pool = pool.ThreadedConnectionPool(
            5 if self.config.getboolean('BOT', 'MASTER') is True else 2,
            10 if self.config.getboolean('BOT', 'MASTER') is True else 5,
            self.config['BOT']['DATABASE_URL'], sslmode='allow')
        if self.config.getboolean('BOT', 'MASTER') is True:
            conn = db_pool.getconn()
            try:
                # check if there is a database already
                db_version = None
                with suppress(Exception):
                    with closing(conn.cursor()) as cursor:
                        cursor.execute(
                            'SELECT count(*) FROM pg_catalog.pg_tables WHERE tablename in (\'servers\', \'version\')')
                        cnt = cursor.fetchone()[0]
                        if cnt > 0:
                            if cnt == 2:
                                cursor.execute('SELECT version FROM version')
                                db_version = cursor.fetchone()[0]
                            elif cnt == 1:
                                db_version = 'v1.0'
                            while path.exists(UPDATES_SQL.format(db_version)):
                                self.log.info('Upgrading Database version {} ...'.format(db_version))
                                with open(UPDATES_SQL.format(db_version)) as tables_sql:
                                    for query in tables_sql.readlines():
                                        self.log.debug(query.rstrip())
                                        cursor.execute(query.rstrip())
                                cursor.execute('SELECT version FROM version')
                                db_version = cursor.fetchone()[0]
                                self.log.info('Database upgraded to version {}.'.format(db_version))
                    # no, create one
                    if db_version is None:
                        self.log.info('Initializing Database ...')
                        with closing(conn.cursor()) as cursor:
                            with open(TABLES_SQL) as tables_sql:
                                for query in tables_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                        self.log.info('Database initialized.')
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
                raise error
            finally:
                db_pool.putconn(conn)
        return db_pool

    def sanitize(self):
        # Sanitizing MissionScripting.lua
        filename = os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) + r'\Scripts\MissionScripting.lua'
        try:
            with open(filename, 'r') as infile:
                orig = infile.readlines()
            output = []
            dirty = False
            for line in orig:
                if ("sanitizeModule('io')" in line or "sanitizeModule('lfs')" in line) and not line.lstrip().startswith(
                        '--'):
                    line = line.replace('sanitizeModule', '--sanitizeModule')
                    dirty = True
                elif 'require = nil' in line and not line.lstrip().startswith('--'):
                    line = line.replace('require', '--require')
                    dirty = True
                output.append(line)
            if dirty:
                self.log.info('- Sanitizing MissionScripting')
                backup = filename.replace('.lua', '.bak')
                # backup original file
                shutil.copyfile(filename, backup)
                with open(filename, 'w') as outfile:
                    outfile.writelines(output)
        except (OSError, IOError) as e:
            self.log.error(f"Can't access {filename}. Make sure, {self.config['DCS']['DCS_INSTALLATION']} is writable.")
            raise e

    def install_hooks(self):
        for installation in utils.findDCSInstallations():
            if installation not in self.config:
                continue
            self.log.info('- Configure DCS installation: {}'.format(installation))
            dcs_path = os.path.expandvars(self.config[installation]['DCS_HOME'] + '\\Scripts')
            assert path.exists(dcs_path), 'Can\'t find DCS installation directory. Exiting.'
            ignore = None
            if path.exists(dcs_path + r'\net\DCSServerBot'):
                self.log.debug('- Updating Hook ...')
                ignore = shutil.ignore_patterns('DCSServerBotConfig.lua.tmpl')
            else:
                self.log.debug('- Installing Hook ...')
            shutil.copytree('./Scripts', dcs_path, dirs_exist_ok=True, ignore=ignore)
            try:
                with open(r'.\Scripts\net\DCSServerBot\DCSServerBotConfig.lua.tmpl', 'r') as template:
                    with open(dcs_path + r'\net\DCSServerBot\DCSServerBotConfig.lua', 'w') as outfile:
                        for line in template.readlines():
                            s = line.find('{')
                            e = line.find('}')
                            if s != -1 and e != -1 and (e - s) > 1:
                                param = line[s + 1:e].split('.')
                                if len(param) == 2:
                                    if param[0] == 'BOT' and param[1] == 'HOST' and self.config[param[0]][param[1]] == '0.0.0.0':
                                        line = line.replace('{' + '.'.join(param) + '}', '127.0.0.1')
                                    else:
                                        line = line.replace('{' + '.'.join(param) + '}', self.config[param[0]][param[1]])
                                elif len(param) == 1:
                                    line = line.replace('{' + '.'.join(param) + '}', self.config[installation][param[0]])
                            outfile.write(line)
            except KeyError as k:
                self.log.error(
                    f'! Your dcsserverbot.ini contains errors. You must set a value for {k}. See README for help.')
                raise k
            self.log.debug('- Hook installed into {}.'.format(installation))

    def init_bot(self):
        def get_prefix(client, message):
            prefixes = [self.config['BOT']['COMMAND_PREFIX']]
            # Allow users to @mention the bot instead of using a prefix
            return commands.when_mentioned_or(*prefixes)(client, message)

        # Create the Bot
        return DCSServerBot(version=BOT_VERSION,
                            command_prefix=get_prefix,
                            description='Interact with DCS World servers',
                            owner_id=int(self.config['BOT']['OWNER']),
                            case_insensitive=True,
                            intents=discord.Intents.all(),
                            log=self.log,
                            config=self.config,
                            pool=self.pool)

    def run(self):
        self.log.info('- Starting {}-Node on {}'.format('Master' if self.config.getboolean(
            'BOT', 'MASTER') is True else 'Agent', platform.node()))
        self.bot.run(self.config['BOT']['TOKEN'], bot=True, reconnect=True)

    def add_commands(self):
        @self.bot.command(description='Reloads a COG', usage='[cog]')
        @commands.is_owner()
        async def reload(ctx, plugin=None):
            self.read_config()
            self.bot.reload(plugin)
            if plugin:
                await ctx.send('COG {} reloaded.'.format(plugin))
            else:
                await ctx.send('All COGs reloaded.')

        @self.bot.command(description='Upgrades the bot')
        @commands.is_owner()
        async def upgrade(ctx):
            await self.upgrade()

    async def upgrade(self):
        try:
            import git

            try:
                with closing(git.Repo('.')) as repo:
                    self.log.debug('Checking for updates...')
                    current_hash = repo.head.commit.hexsha
                    origin = repo.remotes.origin
                    origin.fetch()
                    new_hash = origin.refs[repo.active_branch.name].object.hexsha
                    if new_hash != current_hash:
                        restart = modules = False
                        self.log.info('Remote repo has changed.')
                        self.log.info('Updating myself...')
                        diff = repo.head.commit.diff(new_hash)
                        for d in diff:
                            if d.b_path in ['run.py', 'bot.py', 'const.py', 'listener.py', 'utils.py']:
                                restart = True
                            elif d.b_path == 'requirements.txt':
                                modules = True
                        repo.remote().pull(repo.active_branch)
                        self.log.info('Updated to latest version.')
                        if modules is True:
                            self.log.warning('requirements.txt has changed. Installing missing modules...')
                            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
                        if restart is True:
                            self.log.warning('run.py has changed.\nRestart needed => exiting.')
                            exit(-1)
                    else:
                        self.log.debug('No upgrade found for DCSServerBot.')
            except git.exc.InvalidGitRepositoryError:
                self.log.error('No git repository found. Aborting. Please run the installer again.')
        except ImportError:
            self.log.error('Autoupdate functionality requires "git" executable to be in the PATH.')


if __name__ == "__main__":
    try:
        Main().run()
    except BaseException as e:
        print(e)
        exit(-1)
