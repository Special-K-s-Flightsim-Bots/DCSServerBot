import asyncio
import discord
import logging
import os
import platform
import psycopg2
import psycopg2.extras
import shutil
import string
import subprocess
import sys
from core import utils, DCSServerBot
from core.const import Status
from configparser import ConfigParser
from contextlib import closing, suppress
from discord.ext import commands
from install import Install
from logging.handlers import RotatingFileHandler
from os import path
from psycopg2 import pool


# Set the bot version (not externally configurable)
BOT_VERSION = '2.6.1'
SUB_VERSION = 3

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
        fh = RotatingFileHandler('dcsserverbot.log', encoding='utf-8',
                                 maxBytes=int(self.config['BOT']['LOGROTATE_SIZE']),
                                 backupCount=int(self.config['BOT']['LOGROTATE_COUNT']))
        if 'LOGLEVEL' in self.config['BOT']:
            fh.setLevel(LOGLEVEL[self.config['BOT']['LOGLEVEL']])
        else:
            fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
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
        config['BOT']['SUB_VERSION'] = str(SUB_VERSION)
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
                        cursor.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename IN ('version', 'plugins')")
                        tables = [x[0] for x in cursor.fetchall()]
                        # initial setup
                        if len(tables) == 0:
                            self.log.info('Initializing Database ...')
                            with open(TABLES_SQL) as tables_sql:
                                for query in tables_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                            self.log.info('Database initialized.')
                        else:
                            # version table missing
                            if 'version' not in tables:
                                cursor.execute("CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY);"
                                               "INSERT INTO version (version) VALUES ('v1.4');")
                            cursor.execute('SELECT version FROM version')
                            db_version = cursor.fetchone()[0]
                            while path.exists(UPDATES_SQL.format(db_version)):
                                self.log.info('Updating Database {} ...'.format(db_version))
                                with open(UPDATES_SQL.format(db_version)) as tables_sql:
                                    for query in tables_sql.readlines():
                                        self.log.debug(query.rstrip())
                                        cursor.execute(query.rstrip())
                                cursor.execute('SELECT version FROM version')
                                db_version = cursor.fetchone()[0]
                                self.log.info(f"Database updated to {db_version}.")
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
        for server_name, installation in utils.findDCSInstallations():
            if installation not in self.config:
                continue
            self.log.info(f'  => {installation}')
            if self.config.getboolean(installation, 'COALITIONS'):
                self.log.debug('  - Updating serverSettings.lua ...')
                utils.changeServerSettings(server_name, 'allow_players_pool', False)
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
                            sub_version=SUB_VERSION,
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
        @self.bot.command(description='Reloads a Plugin', usage='[cog]')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def reload(ctx, plugin=None):
            self.read_config()
            self.bot.reload(plugin)
            if plugin:
                await ctx.send('Plugin {} reloaded.'.format(string.capwords(plugin)))
            else:
                await ctx.send('All plugins reloaded.')

        @self.bot.command(description='Lists all installed plugins')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def plugins(ctx):
            embed = discord.Embed(color=discord.Color.blue())
            embed.add_field(name=f'The following plugins are installed on node {platform.node()}:',
                            value='\n'.join([string.capwords(x) for x in self.bot.plugins]))
            embed.set_footer(text=f"Bot Version: v{self.bot.version}.{self.bot.sub_version}")
            await ctx.send(embed=embed)

        @self.bot.command(description='Rename a server')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def rename(ctx, *args):
            server = await utils.get_server(self.bot, ctx)
            if server:
                old_name = server['server_name']
                new_name = ' '.join(args)
                if len(new_name) == 0:
                    await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}rename <new server name>")
                    return
                if server['status'] in [Status.STOPPED, Status.SHUTDOWN]:
                    if await utils.yn_question(self, ctx, 'Are you sure to rename server "{}" '
                                                          'to "{}"?'.format(old_name, new_name)) is True:
                        self.bot.rename_server(old_name, new_name, True)
                        await ctx.send('Server has been renamed.')
                        await self.bot.audit(
                            f'User {ctx.message.author.display_name} renamed DCS server "{old_name}" to "{new_name}".',
                            user=ctx.message.author)
                else:
                    await ctx.send('Please stop server "{}" before renaming!'.format(old_name))

        @self.bot.command(description='Unregisters the server from this instance')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def unregister(ctx):
            server = await utils.get_server(self.bot, ctx)
            if server:
                server_name = server['server_name']
                if server['status'] == Status.SHUTDOWN:
                    if await utils.yn_question(self, ctx, 'Are you sure to unregister server "{}" from '
                                                          'node "{}"?'.format(server_name, platform.node())) is True:
                        del self.bot.globals[server_name]
                        del self.bot.embeds[server_name]
                        await ctx.send('Server {} unregistered.'.format(server_name))
                        await self.bot.audit(f"User {ctx.message.author.display_name} unregistered DCS server "
                                             f"\"{server['server_name']}\" from node {platform.node()}.",
                                             user=ctx.message.author)
                    else:
                        await ctx.send('Aborted.')
                else:
                    await ctx.send('Please shut down server "{}" before unregistering!'.format(server_name))

        @self.bot.command(description='Upgrades the bot')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def upgrade(ctx):
            if await utils.yn_question(self, ctx, 'The bot will check and upgrade to the latest version, '
                                                  'if available.\nAre you sure?') is True:
                await ctx.send('Checking for a bot upgrade ...')
                if self.upgrade():
                    await ctx.send('The bot has upgraded itself.')
                    running = False
                    for server_name, server in self.bot.globals.items():
                        if server['status'] != Status.SHUTDOWN:
                            running = True
                    if running and await utils.yn_question(self, ctx, 'It is recommended to shut down all running '
                                                                      'servers.\nWould you like to shut them down now ('
                                                                      'Y/N)?') is True:
                        for server_name, server in self.bot.globals.items():
                            self.bot.sendtoDCS(server, {"command": "shutdown", "channel": ctx.channel.id})
                        await asyncio.sleep(5)
                    await ctx.send('The bot is now restarting itself.\nAll servers will be launched according to their '
                                   'scheduler configuration on bot start.')
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
                self.log.error('No git repository found. Aborting. Please use "git clone" to install DCSServerBot.')
        except ImportError:
            self.log.error('Autoupdate functionality requires "git" executable to be in the PATH.')
        return False


if __name__ == "__main__":
    if not path.exists('config/dcsserverbot.ini'):
        Install.install()
    else:
        try:
            Install.verify()
            Main().run()
        except discord.errors.LoginFailure:
            print('Invalid Discord TOKEN provided. Please check the documentation.')
        except Exception as ex:
            print(f"{ex.__class__.__name__}: {ex}")
            exit(-1)
