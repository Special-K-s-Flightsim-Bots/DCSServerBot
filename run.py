from __future__ import annotations
import asyncio
import aiohttp
import aiofiles
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
import zipfile
from core import utils, Server, DCSServerBot, Status
from contextlib import closing, suppress
from discord import SelectOption
from discord.ext import commands
from install import Install
from logging.handlers import RotatingFileHandler
from matplotlib import font_manager
from pathlib import Path
from psycopg2 import pool
from typing import Optional, TYPE_CHECKING
from version import __version__

if TYPE_CHECKING:
    from core import Plugin


# Set the bot version (not externally configurable)
BOT_VERSION = __version__[:__version__.rfind('.')]
SUB_VERSION = int(__version__[__version__.rfind('.') + 1:])

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'FATAL': logging.FATAL
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
        self.log.info(f'- Python version {platform.python_version()} detected.')
        if self.config.getboolean('BOT', 'AUTOUPDATE') and self.upgrade():
            self.log.warning('- Restart needed => exiting.')
            exit(-1)
        self.db_version = None
        self.install_plugins()
        self.pool = self.init_db()
        utils.desanitize(self)
        self.install_hooks()
        self.install_fonts()
        self.bot: DCSServerBot = self.init_bot()
        self.add_commands()

    def init_logger(self):
        # Initialize the logger
        log = logging.getLogger(name='dcsserverbot')
        log.setLevel(logging.DEBUG)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(threadName)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        fh = RotatingFileHandler('dcsserverbot.log', encoding='utf-8',
                                 maxBytes=int(self.config['BOT']['LOGROTATE_SIZE']),
                                 backupCount=int(self.config['BOT']['LOGROTATE_COUNT']))
        if 'LOGLEVEL' in self.config['BOT']:
            fh.setLevel(LOGLEVEL[self.config['BOT']['LOGLEVEL']])
        else:
            fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        fh.doRollover()
        log.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        log.addHandler(ch)
        return log

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

    @staticmethod
    def read_config():
        config = utils.config
        config['BOT']['VERSION'] = BOT_VERSION
        config['BOT']['SUB_VERSION'] = str(SUB_VERSION)
        return config

    def init_db(self):
        # Initialize the database
        pool_min = self.config['BOT']['MASTER_POOL_MIN'] if self.config.getboolean('BOT', 'MASTER') else self.config['BOT']['AGENT_POOL_MIN']
        pool_max = self.config['BOT']['MASTER_POOL_MAX'] if self.config.getboolean('BOT', 'MASTER') else self.config['BOT']['AGENT_POOL_MAX']
        db_pool = pool.ThreadedConnectionPool(pool_min, pool_max, self.config['BOT']['DATABASE_URL'], sslmode='allow')
        conn = db_pool.getconn()
        try:
            with suppress(Exception):
                with closing(conn.cursor()) as cursor:
                    if self.config.getboolean('BOT', 'MASTER') is True:
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
                            self.db_version = cursor.fetchone()[0]
                            while os.path.exists(UPDATES_SQL.format(self.db_version)):
                                self.log.info('Updating Database {} ...'.format(self.db_version))
                                with open(UPDATES_SQL.format(self.db_version)) as tables_sql:
                                    for query in tables_sql.readlines():
                                        self.log.debug(query.rstrip())
                                        cursor.execute(query.rstrip())
                                cursor.execute('SELECT version FROM version')
                                self.db_version = cursor.fetchone()[0]
                                self.log.info(f"Database updated to {self.db_version}.")
                    else:
                        cursor.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename = 'servers'")
                        if cursor.rowcount == 0:
                            self.log.error('No MASTER database found. Please check configuration!')
                            exit(-1)
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
            dcs_path = os.path.expandvars(self.config[installation]['DCS_HOME'] + '\\Scripts')
            if not os.path.exists(dcs_path):
                os.mkdir(dcs_path)
            ignore = None
            if os.path.exists(dcs_path + r'\net\DCSServerBot'):
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

    def install_fonts(self):
        if 'CJK_FONT' in self.config['REPORTS']:
            if not os.path.exists('fonts'):
                os.makedirs('fonts')

                async def fetch_file(url: str):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as resp:
                            assert resp.status == 200
                            data = await resp.read()

                    async with aiofiles.open(
                            os.path.join('fonts', "temp.zip"), "wb") as outfile:
                        await outfile.write(data)

                    with zipfile.ZipFile('fonts/temp.zip', 'r') as zip_ref:
                        zip_ref.extractall('fonts')

                    os.remove('fonts/temp.zip')
                    for font in font_manager.findSystemFonts('fonts'):
                        font_manager.fontManager.addfont(font)
                    self.log.info('- CJK font installed and loaded.')

                fonts = {
                    "TC": "https://fonts.google.com/download?family=Noto%20Sans%20TC",
                    "JP": "https://fonts.google.com/download?family=Noto%20Sans%20JP",
                    "KR": "https://fonts.google.com/download?family=Noto%20Sans%20KR"
                }

                asyncio.get_event_loop().create_task(fetch_file(fonts[self.config['REPORTS']['CJK_FONT']]))
            else:
                for font in font_manager.findSystemFonts('fonts'):
                    font_manager.fontManager.addfont(font)
                self.log.debug('- CJK fonts loaded.')

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
                            pool=self.pool,
                            help_command=None)

    async def run(self):
        self.log.info('- Starting {}-Node on {}'.format('Master' if self.config.getboolean(
            'BOT', 'MASTER') is True else 'Agent', platform.node()))
        async with self.bot:
            await self.bot.start(self.config['BOT']['TOKEN'], reconnect=True)

    def add_commands(self):

        @self.bot.command(description='Reloads plugins', aliases=['plugins'])
        @utils.has_role('Admin')
        @commands.guild_only()
        async def reload(ctx, cog: Optional[str] = None):
            if cog:
                cogs = [cog.lower()]
            else:
                plugins = list(self.bot.cogs.values())
                embed = discord.Embed(title=f'Installed Plugins ({platform.node()})', color=discord.Color.blue())
                names = versions = ''
                for plugin in plugins:  # type: Plugin
                    names += string.capwords(plugin.plugin_name) + '\n'
                    versions += plugin.plugin_version + '\n'
                embed.add_field(name='Name', value=names)
                embed.add_field(name='Version', value=versions)
                embed.add_field(name='▬' * 20, value='_ _', inline=False)
                embed.add_field(name='Bot Version', value=f"v{self.bot.version}.{self.bot.sub_version}")
                embed.add_field(name='_ _', value='_ _')
                embed.add_field(name='DB Version', value=f"{self.db_version}")
                cogs = await utils.selection(ctx, placeholder="Select the plugin(s) to reload",
                                             embed=embed,
                                             options=[
                                                 SelectOption(
                                                     label=string.capwords(x.plugin_name),
                                                     value=x.plugin_name) for x in plugins
                                             ],
                                             max_values=len(plugins))
                if not cogs:
                    return
            self.read_config()
            for cog in cogs:
                try:
                    await self.bot.reload(cog)
                    await ctx.send(f'Plugin {string.capwords(cog)} reloaded.')
                except commands.ExtensionNotLoaded:
                    await ctx.send(f'Plugin {string.capwords(cog)} not found.')

        @self.bot.command(description='Rename a server')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def rename(ctx, *args):
            server: Server = await self.bot.get_server(ctx)
            if server:
                old_name = server.name
                new_name = ' '.join(args)
                if len(new_name) == 0:
                    await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}rename <new server name>")
                    return
                if server.status not in [Status.RUNNING, Status.PAUSED]:
                    if await utils.yn_question(ctx, 'Are you sure to rename server '
                                                    '"{}" to "{}"?'.format(utils.escape_string(old_name),
                                                                           utils.escape_string(new_name))) is True:
                        server.rename(new_name, True)
                        self.bot.servers[new_name] = server
                        del self.bot.servers[old_name]
                        await ctx.send('Server has been renamed.')
                        await self.bot.audit('renamed DCS server "{}" to "{}".'.format(utils.escape_string(old_name),
                                                                                       utils.escape_string(new_name)),
                                             user=ctx.message.author)
                else:
                    await ctx.send(f'Please stop server "{server.display_name}" before renaming!')

        @self.bot.command(description='Unregisters a server from this node')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def unregister(ctx):
            server: Server = await self.bot.get_server(ctx)
            if server:
                if server.status == Status.SHUTDOWN:
                    if await utils.yn_question(ctx, f'Are you sure to unregister server "{server.display_name}" from '
                                                    f'node "{platform.node()}"?') is True:
                        del self.bot.servers[server.name]
                        await ctx.send(f'Server {server.display_name} unregistered.')
                        await self.bot.audit(
                            f"unregistered DCS server \"{server.display_name}\" from node {platform.node()}.",
                            user=ctx.message.author)
                    else:
                        await ctx.send('Aborted.')
                else:
                    await ctx.send(f'Please shut down server "{server.display_name}" before unregistering!')

        @self.bot.command(description='Upgrades the bot')
        @utils.has_role('Admin')
        @commands.guild_only()
        async def upgrade(ctx):
            if await utils.yn_question(ctx, 'The bot will check and upgrade to the latest version, if available.\n'
                                            'Are you sure?'):
                await ctx.send('Checking for a bot upgrade ...')
                if self.upgrade():
                    await ctx.send('The bot has upgraded itself.')
                    running = False
                    for server_name, server in self.bot.servers.items():
                        if server.status != Status.SHUTDOWN:
                            running = True
                    if running and await utils.yn_question(ctx, 'It is recommended to shut down all running servers.\n'
                                                                'Would you like to shut them down now?'):
                        for server_name, server in self.bot.servers.items():
                            await server.shutdown()
                    await ctx.send('The bot is now restarting itself.\nAll servers will be launched according to their '
                                   'scheduler configuration on bot start.')
                    exit(-1)
                else:
                    await ctx.send('No bot upgrade found.')

        @self.bot.command(description='Terminates the bot process', aliases=['exit'])
        @utils.has_role('Admin')
        @commands.guild_only()
        async def terminate(ctx):
            if await utils.yn_question(ctx, f'Do you really want to terminate the bot on node {platform.node()}?'):
                await ctx.send('Bot will terminate now (and restart automatically, if started by run.cmd).')
                exit(-1)

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
                        self.log.info('- Updating myself...')
                        diff = repo.head.commit.diff(new_hash)
                        for d in diff:
                            if d.b_path == 'requirements.txt':
                                modules = True
                        try:
                            repo.remote().pull(repo.active_branch)
                            self.log.info('  => DCSServerBot updated to latest version.')
                            if modules is True:
                                self.log.warning('  => requirements.txt has changed. Installing missing modules...')
                                subprocess.check_call([sys.executable, '-m', 'pip', '-q', 'install', '-r',
                                                       'requirements.txt'])
                            return True
                        except git.exc.GitCommandError:
                            self.log.error('  => Autoupdate failed!')
                            self.log.error('     Please revert back the changes in these files:')
                            for item in repo.index.diff(None):
                                self.log.error(f'     ./{item.a_path}')
                            return False
                    else:
                        self.log.debug('- No update found for DCSServerBot.')
            except git.exc.InvalidGitRepositoryError:
                self.log.error('No git repository found. Aborting. Please use "git clone" to install DCSServerBot.')
        except ImportError:
            self.log.error('Autoupdate functionality requires "git" executable to be in the PATH.')
        return False


async def main():
    if not os.path.exists('config/dcsserverbot.ini'):
        print("Please run 'python install.py' first.")
    else:
        Install.verify()
        await Main().run()

if __name__ == "__main__":
    if int(platform.python_version_tuple()[0]) != 3 or int(platform.python_version_tuple()[1]) not in range(9, 12):
        print("You need Python 3.9 to 3.11 to run DCSServerBot!")
        exit(-1)
    try:
        asyncio.run(main())
    except discord.errors.LoginFailure:
        print('Invalid Discord TOKEN provided. Please check the documentation.')
    except KeyboardInterrupt:
        exit(-1)
    except Exception as ex:
        print(f"{ex.__class__.__name__}: {ex}")
        exit(-1)
