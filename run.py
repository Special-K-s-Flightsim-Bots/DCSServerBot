# run.py
import asyncio
import discord
import logging
import os
import platform
import psycopg2
import psycopg2.extras
import shutil
import subprocess
import sys
import winreg
from core import utils, DCSServerBot
from core.const import Status
from configparser import ConfigParser
from contextlib import closing, suppress
from discord.ext import commands
from logging.handlers import RotatingFileHandler
from os import path
from psycopg2 import pool

# Set the bot's version (not externally configurable)
BOT_VERSION = '2.5.7'
SUB_VERSION = 4

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
        self.log.info(f'DCSServerBot v{BOT_VERSION}.{SUB_VERSION} starting up ...')
        if self.config.getboolean('BOT', 'AUTOUPDATE') and self.upgrade():
            self.log.warning('- Restart needed => exiting.')
            exit(-1)
        self.pool = self.init_db()
        utils.sanitize(self)
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
        ch.setLevel(logging.INFO)
        log.addHandler(ch)
        return log

    @staticmethod
    def read_config():
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
                with suppress(Exception):
                    with closing(conn.cursor()) as cursor:
                        # check if there is an old database already
                        cursor.execute(
                            "SELECT count(*) FROM pg_catalog.pg_tables WHERE tablename = 'version'")
                        while cursor.fetchone()[0] == 1:
                            cursor.execute('SELECT version FROM version')
                            db_version = cursor.fetchone()[0]
                            if path.exists(UPDATES_SQL.format(db_version)):
                                self.log.info('Updating Database version {} ...'.format(db_version))
                                with open(UPDATES_SQL.format(db_version)) as tables_sql:
                                    for query in tables_sql.readlines():
                                        self.log.debug(query.rstrip())
                                        cursor.execute(query.rstrip())
                                self.log.info('Database updated.'.format(db_version))
                            cursor.execute(
                                "SELECT count(*) FROM pg_catalog.pg_tables WHERE tablename = 'version'")
                        # check if the new database is already setup
                        cursor.execute(
                            "SELECT count(*) FROM pg_catalog.pg_tables WHERE tablename = 'plugins'")
                        if cursor.fetchone()[0] == 0:
                            self.log.info('Initializing Database ...')
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
        # Make sure we only get back floats, not Decimal
        dec2float = psycopg2.extensions.new_type(
            psycopg2.extensions.DECIMAL.values,
            'DEC2FLOAT',
            lambda value, curs: float(value) if value is not None else None)
        psycopg2.extensions.register_type(dec2float)
        return db_pool

    def install_hooks(self):
        self.log.info('- Configure DCS installations ...')
        for installation in utils.findDCSInstallations():
            if installation not in self.config:
                continue
            self.log.info(f'  => {installation}')
            dcs_path = os.path.expandvars(self.config[installation]['DCS_HOME'] + '\\Scripts')
            if not path.exists(dcs_path):
                os.mkdir(dcs_path)
            ignore = None
            if path.exists(dcs_path + r'\net\DCSServerBot'):
                self.log.debug('  - Updating Hooks ...')
                shutil.rmtree(dcs_path + r'\net\DCSServerBot')
                ignore = shutil.ignore_patterns('DCSServerBotConfig.lua.tmpl')
            else:
                self.log.debug('  - Installing Hooks ...')
            shutil.copytree('./Scripts', dcs_path, dirs_exist_ok=True, ignore=ignore)
            try:
                with open(r'Scripts/net/DCSServerBot/DCSServerBotConfig.lua.tmpl', 'r') as template:
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
            self.log.debug('  - Hooks installed into {}.'.format(installation))

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
            if await utils.yn_question(self, ctx, 'The bot will check and upgrade to the latest version, '
                                                  'if available.\nAre you sure?') is True:
                await ctx.send('Checking for a bot upgrade ...')
                if self.upgrade():
                    await ctx.send('The bot has upgraded itself.')
                    running = False
                    for server_name, server in self.bot.globals.items():
                        if server['status'] in [Status.RUNNING, Status.PAUSED]:
                            running = True
                    if running and await utils.yn_question(self, ctx, 'It is recommended to shut down all running '
                                                                      'servers.\nWould you like to shut them down now ('
                                                                      'Y/N)?') is True:
                        for server_name, server in self.bot.globals.items():
                            self.bot.sendtoDCS(server, {"command": "shutdown", "channel": ctx.channel.id})
                            server['status'] = Status.SHUTDOWN
                        await asyncio.sleep(5)
                    await ctx.send('The bot is now restarting itself.\nAll servers with AUTOSTART_DCS=true will be '
                                   'launched on bot start.')
                    exit(-1)
                else:
                    await ctx.send('No bot upgrade found.')

    def upgrade(self) -> bool:
        try:
            import git

            try:
                with closing(git.Repo('.')) as repo:
                    self.log.debug('- Checking for updates...')
                    current_hash = repo.head.commit.hexsha
                    origin = repo.remotes.origin
                    origin.fetch()
                    new_hash = origin.refs[repo.active_branch.name].object.hexsha
                    if new_hash != current_hash:
                        modules = False
                        self.log.info('  => Remote repo has changed.')
                        self.log.info('  => Updating myself...')
                        diff = repo.head.commit.diff(new_hash)
                        for d in diff:
                            if d.b_path == 'requirements.txt':
                                modules = True
                        repo.remote().pull(repo.active_branch)
                        self.log.info('- DCSServerBot updated to latest version.')
                        if modules is True:
                            self.log.warning('- requirements.txt has changed. Installing missing modules...')
                            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
                        return True
                    else:
                        self.log.debug('- No upgrade found for DCSServerBot.')
            except git.exc.InvalidGitRepositoryError:
                self.log.error('No git repository found. Aborting. Please run the installer again.')
        except ImportError:
            self.log.error('Autoupdate functionality requires "git" executable to be in the PATH.')
        return False

    @staticmethod
    def install():
        print('Welcome to the DCSSeverBot!\n\nI will create a file named config/dcsserverbot.ini for you now...')
        dcs_installation = None
        key = skey = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics", 0)
            num_dcs_installs = winreg.QueryInfoKey(key)[0]
            if num_dcs_installs == 0:
                print('Attention: No installation of DCS World found on this PC. Autostart will not work.')
            elif num_dcs_installs > 1:
                print('I\'ve found multiple installations of DCS World on this PC:')
                for i in range(0, num_dcs_installs):
                    print(f'{i+1}: {winreg.EnumKey(key, i)}')
                num = int(input('\nPlease specify, which installation you want the bot to use: '))
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{winreg.EnumKey(key, num-1)}", 0)
            else:
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{winreg.EnumKey(key, 0)}", 0)
            if skey:
                dcs_installation = winreg.QueryValueEx(skey, 'Path')[0]
                if not path.exists(dcs_installation):
                    dcs_installation = None
                    raise OSError
        except FileNotFoundError:
            print('Attention: No installation of DCS World found on this PC. Autostart will not work.')
        except OSError:
            print('Attention: Your DCS was not installed correctly. Autostart will not work.')
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()

        with open('config/dcsserverbot.ini', 'w') as inifile:
            inifile.writelines([
                '; This file is generated and has to be amended to your needs!\n',
                '[BOT]\n',
                'OWNER=<see documentation>\n',
                'TOKEN=<see documentation>\n',
                'DATABASE_URL=postgres://<user>:<pass>@localhost:5432/<database>\n',
                '\n'])
            if dcs_installation:
                inifile.writelines([
                    '[DCS]\n',
                    'DCS_INSTALLATION={}\n'.format(dcs_installation.replace('\\', '\\\\')),
                    '\n'
                ])
            dcs_port = 6666
            for installation in utils.findDCSInstallations():
                inifile.writelines([
                    f'[{installation}]\n',
                    'DCS_HOST=127.0.0.1\n',
                    f'DCS_PORT={dcs_port}\n',
                    r'DCS_HOME = %%USERPROFILE%%\\Saved Games\\' + f'{installation}\n',
                    'AUTOSTART_DCS=false\n',
                    'ADMIN_CHANNEL=<see documentation>\n',
                    'STATUS_CHANNEL=<see documentation>\n',
                    'CHAT_CHANNEL=<see documentation>\n',
                    '\n'
                ])
                dcs_port += 1
        print('Please check config/dcsserverbot.ini and edit it according to the installation documentation before '
              'you restart the bot.')


if __name__ == "__main__":
    if not path.exists('config/dcsserverbot.ini'):
        Main.install()
    else:
        try:
            Main().run()
        except psycopg2.OperationalError:
            print('Can\'t connect to the database. Please check your dcsserverbot.ini for any error in the DATABASE_URL!')
        except Exception as e:
            print(e.__repr__())
            exit(-1)
