import aiofiles
import aiohttp
import asyncio
import discord
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from contextlib import closing
from core import utils, ServiceRegistry, DataObjectFactory, Instance, Server
from datetime import datetime
from logging.handlers import RotatingFileHandler
from matplotlib import font_manager
from pathlib import Path
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from services import Dashboard, BotService
from typing import cast, Optional, Union
from version import __version__


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


class Node:

    def __init__(self):
        self.name: str = platform.node()
        self.config = self.read_config()
        self.guild_id: int = int(self.config['BOT']['GUILD_ID'])
        self._public_ip: Optional[str] = None
        self.log = self.init_logger()
        if self.config.getboolean('BOT', 'AUTOUPDATE') and self.upgrade():
            self.log.warning('- Restart needed => exiting.')
            exit(-1)
        self.log.info(f'DCSServerBot v{BOT_VERSION}.{SUB_VERSION} starting up ...')
        self.log.info(f'- Python version {platform.python_version()} detected.')
        self.db_version = None
        self.pool = self.init_db()
        self.instances: list[Instance] = list()
        self.locals: dict = self.read_locals()
        self.install_plugins()
        plugins: str = self.config['BOT']['PLUGINS']
        if 'OPT_PLUGINS' in self.config['BOT']:
            plugins += ', ' + self.config['BOT']['OPT_PLUGINS']
        self.plugins: [str] = [p.strip() for p in list(dict.fromkeys(plugins.split(',')))]
        # make sure, cloud is loaded last
        if 'cloud' in self.plugins:
            self.plugins.remove('cloud')
            self.plugins.append('cloud')
        try:
            self._master = self.check_master()
        except UndefinedTable:
            # should only happen when an upgrade to 3.0 is needed
            self.log.info("Updating database to DCSServerBot 3.x ...")
            self._master = True
        if self._master:
            self.update_db()

    @property
    def master(self) -> bool:
        return self._master

    @master.setter
    def master(self, value: bool):
        self._master = value

    @property
    def public_ip(self) -> str:
        return self._public_ip

    @property
    def installation(self) -> str:
        return os.path.expandvars(self.locals['DCS']['installation'])

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions', {})

    async def audit(self, message, *, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None):
        if self.master:
            await ServiceRegistry.get("Bot").bot.audit(message, user=user, server=server)
        else:
            ServiceRegistry.get("ServiceBus").sendtoBot({
                "command": "rpc",
                "service": "Bot",
                "method": "audit",
                "params": {
                    "message": message,
                    "user": user,
                    "server_name": server.name if server else ""
                }
            })

    def add_instance(self, instance: Instance):
        raise NotImplementedError()

    def del_instance(self, name: str):
        raise NotImplementedError()

    @staticmethod
    def read_config():
        config = utils.config
        config['BOT']['VERSION'] = BOT_VERSION
        config['BOT']['SUB_VERSION'] = str(SUB_VERSION)
        return config

    def read_locals(self) -> dict:
        _locals = dict()
        if os.path.exists('config/nodes.json'):
            with open('config/nodes.json') as infile:
                node: dict = json.load(infile)[self.name]
                for name, element in node.items():
                    if name == 'instances':
                        for _name, _element in node['instances'].items():
                            instance: Instance = DataObjectFactory().new(Instance.__name__, node=self, name=_name)
                            instance.locals = _element
                            self.instances.append(instance)
                    else:
                        _locals[name] = element
        return _locals

    def init_logger(self):
        log = logging.getLogger(name='dcsserverbot')
        log.setLevel(logging.DEBUG)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        formatter.converter = time.gmtime
        fh = RotatingFileHandler(f'dcssb-{self.name}.log', encoding='utf-8',
                                 maxBytes=int(self.config['LOGGING']['LOGROTATE_SIZE']),
                                 backupCount=int(self.config['LOGGING']['LOGROTATE_COUNT']))
        fh.setLevel(LOGLEVEL[self.config['LOGGING']['LOGLEVEL']])
        fh.setFormatter(formatter)
        fh.doRollover()
        log.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        log.addHandler(ch)
        return log

    def init_db(self):
        db_pool = ConnectionPool(self.config['BOT']['DATABASE_URL'],
                                 min_size=int(self.config['DB']['MASTER_POOL_MIN']),
                                 max_size=int(self.config['DB']['MASTER_POOL_MAX']))
        return db_pool

    def update_db(self):
        # Initialize the database
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    # check if there is an old database already
                    cursor.execute("SELECT tablename FROM pg_catalog.pg_tables "
                                   "WHERE tablename IN ('version', 'plugins')")
                    tables = [x[0] for x in cursor.fetchall()]
                    # initial setup
                    if len(tables) == 0:
                        self.log.info('Initializing Database ...')
                        with open('tables.sql') as tables_sql:
                            for query in tables_sql.readlines():
                                self.log.debug(query.rstrip())
                                cursor.execute(query.rstrip())
                        self.log.info('Database initialized.')
                    else:
                        # version table missing (DB version <= 1.4)
                        if 'version' not in tables:
                            cursor.execute("CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY);"
                                           "INSERT INTO version (version) VALUES ('v1.4');")
                        cursor.execute('SELECT version FROM version')
                        self.db_version = cursor.fetchone()[0]
                        while os.path.exists('sql/update_{}.sql'.format(self.db_version)):
                            self.log.info('Updating Database {} ...'.format(self.db_version))
                            with open('sql/update_{}.sql'.format(self.db_version)) as tables_sql:
                                for query in tables_sql.readlines():
                                    self.log.debug(query.rstrip())
                                    cursor.execute(query.rstrip())
                            cursor.execute('SELECT version FROM version')
                            self.db_version = cursor.fetchone()[0]
                            self.log.info(f"Database updated to {self.db_version}.")
        # Make sure we only get back floats, not Decimal
# TODO
#        dec2float = psycopg.extensions.new_type(
#            psycopg.extensions.DECIMAL.values,
#            'DEC2FLOAT',
#            lambda value, curs: float(value) if value is not None else None)
#        psycopg.extensions.register_type(dec2float)

    async def install_fonts(self):
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

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

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

    async def register(self):
        self._public_ip = self.locals.get('public_ip', await utils.get_public_ip())
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("INSERT INTO nodes (guild_id, node, master) VALUES (%s, %s, False) "
                             "ON CONFLICT (guild_id, node) DO NOTHING", (self.guild_id, self.name))

    def check_master(self) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor(row_factory=dict_row)) as cursor:
                    master = False
                    count = 0
                    cursor.execute("SELECT * FROM nodes WHERE guild_id = %s FOR UPDATE", (self.guild_id, ))
                    for row in cursor.fetchall():
                        if row['master']:
                            count += 1
                            if row['node'] == self.name:
                                master = True
                            # the old master is dead, we probably need to take over
                            elif (datetime.now() - row['last_seen']).total_seconds() > 10:
                                self.log.debug(f"- Master {row['node']} was last seen on {row['last_seen']}")
                                cursor.execute('UPDATE nodes SET master = False WHERE guild_id = %s and node = %s',
                                               (self.guild_id, row['node']))
                                count -= 1
                    # no master there, we're the master now
                    if count == 0:
                        cursor.execute('UPDATE nodes SET master = True, last_seen = NOW() '
                                       'WHERE guild_id = %s and node = %s',
                                       (self.guild_id, self.name))
                        master = True
                    # there is only one master, might be me, might be others
                    elif count == 1:
                        cursor.execute('UPDATE nodes SET master = %s, last_seen = NOW() '
                                       'WHERE guild_id = %s and node = %s',
                                       (master, self.guild_id, self.name))
                    # split brain detected, so step back
                    else:
                        self.log.warning("Split brain detected, stepping back from master.")
                        cursor.execute('UPDATE nodes SET master = False, last_seen = NOW() '
                                       'WHERE guild_id = %s and node = %s',
                                       (self.guild_id, self.name))
                        master = False
            return master

    def get_active_nodes(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    return [row[0] for row in cursor.execute("""
                        SELECT node FROM nodes 
                        WHERE guild_id = %s
                        AND master is False 
                        AND last_seen > (DATE(NOW()) - interval '1 minute')
                    """, (self.guild_id, ))]

    async def run(self):
        await self.register()
        async with ServiceRegistry(node=self) as registry:
            bus = registry.new("ServiceBus")
            if self.master:
                await self.install_fonts()
                # config = registry.new("Configuration")
                # asyncio.create_task(config.start())
                bot = cast(BotService, registry.new("Bot"))
                asyncio.create_task(bot.start(token=self.config['BOT']['TOKEN']))
            asyncio.create_task(bus.start())
            asyncio.create_task(registry.new("Monitoring").start())
            try:
                asyncio.create_task(registry.new("Backup").start())
            except Exception as ex:
                self.log.exception(ex)
                return
            if self.config['BOT'].getboolean('USE_DASHBOARD'):
                dashboard = cast(Dashboard, registry.new("Dashboard"))
                asyncio.create_task(dashboard.start())
            while True:
                # wait until the master changes
                while self.master == self.check_master():
                    await asyncio.sleep(1)
                # switch master
                self.master = not self.master
                if self.master:
                    self.log.info("Master is not responding... taking over.")
                    if self.config['BOT'].getboolean('USE_DASHBOARD'):
                        await dashboard.stop()
                    await self.install_fonts()
                    # config = registry.new("Configuration")
                    # asyncio.create_task(config.start())
                    bot = cast(BotService, registry.new("Bot"))
                    asyncio.create_task(bot.start(token=self.config['BOT']['TOKEN']))
                else:
                    self.log.info("Second Master found, stepping back to Agent configuration.")
                    if self.config['BOT'].getboolean('USE_DASHBOARD'):
                        await dashboard.stop()
                    # await config.stop()
                    await bot.stop()
                if self.config['BOT'].getboolean('USE_DASHBOARD'):
                    await dashboard.start()
