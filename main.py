import aiofiles
import aiohttp
import asyncio
import logging
import os
import platform
import psycopg2
import shutil
import zipfile
from contextlib import closing, suppress
from core import utils, ServiceRegistry
from core.pool import ThreadedConnectionPool
from datetime import datetime
from logging.handlers import RotatingFileHandler
from matplotlib import font_manager
from pathlib import Path
from psycopg2.extras import RealDictCursor
from services import Dashboard, BotService, EventListenerService
from typing import cast
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


class Main:

    def __init__(self):
        self.config = self.read_config()
        self.guild_id = int(self.config['BOT']['GUILD_ID'])
        self.log = self.init_logger()
        self.db_version = None
        self.pool = self.init_db()
        if self.config['BOT'].getboolean('DESANITIZE'):
            utils.desanitize(self)
        self.install_hooks()
        self.register()
        if self.is_master():
            self.install_plugins()
            self.update_db()

    @staticmethod
    def read_config():
        config = utils.config
        config['BOT']['VERSION'] = BOT_VERSION
        config['BOT']['SUB_VERSION'] = str(SUB_VERSION)
        return config

    def init_logger(self):
        log = logging.getLogger(name='dcsserverbot')
        log.setLevel(logging.DEBUG)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        fh = RotatingFileHandler(f'dcssb-{platform.node()}.log', encoding='utf-8',
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
        db_pool = ThreadedConnectionPool(int(self.config['DB']['MASTER_POOL_MIN']),
                                         int(self.config['DB']['MASTER_POOL_MAX']),
                                         self.config['BOT']['DATABASE_URL'],
                                         sslmode='allow')
        return db_pool

    def update_db(self):
        # Initialize the database
        conn = self.pool.getconn()
        try:
            with suppress(Exception):
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
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
            raise error
        finally:
            self.pool.putconn(conn)
        # Make sure we only get back floats, not Decimal
        dec2float = psycopg2.extensions.new_type(
            psycopg2.extensions.DECIMAL.values,
            'DEC2FLOAT',
            lambda value, curs: float(value) if value is not None else None)
        psycopg2.extensions.register_type(dec2float)

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
                                    if param[0] == 'BOT' and param[1] == 'HOST' and \
                                            self.config[param[0]][param[1]] == '0.0.0.0':
                                        line = line.replace('{' + '.'.join(param) + '}', '127.0.0.1')
                                    else:
                                        line = line.replace('{' + '.'.join(param) + '}',
                                                            self.config[param[0]][param[1]])
                                elif len(param) == 1:
                                    line = line.replace('{' + '.'.join(param) + '}',
                                                        self.config[installation][param[0]])
                            outfile.write(line)
            except KeyError as k:
                self.log.error(
                    f'! Your dcsserverbot.ini contains errors. You must set a value for {k}. See README for help.')
                raise k
            self.log.debug('  - Hooks installed into {}.'.format(installation))

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

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

    def register(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO agents (guild_id, node, master) VALUES (%s, %s, False) "
                               "ON CONFLICT (guild_id, node) DO UPDATE SET master = False",
                               (self.guild_id, platform.node()))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
            raise error
        finally:
            self.pool.putconn(conn)

    def is_master(self) -> bool:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=RealDictCursor)) as cursor:
                cursor.execute("UPDATE agents SET last_seen = NOW() WHERE guild_id = %s and node = %s",
                               (self.guild_id, platform.node()))
                cursor.execute("SELECT * FROM agents WHERE guild_id = %s FOR UPDATE SKIP LOCKED", (self.guild_id, ))
                for row in cursor.fetchall():
                    if row['master']:
                        if row['node'] == platform.node():
                            return True
                        elif (datetime.now() - row['last_seen']).seconds < 60:
                            return False
                cursor.execute('UPDATE agents SET master = False WHERE guild_id = %s', (self.guild_id, ))
                cursor.execute('UPDATE agents SET master = True WHERE guild_id = %s and node = %s',
                               (self.guild_id, platform.node()))
            conn.commit()
            return True

        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
            raise error
        finally:
            self.pool.putconn(conn)

    async def run(self):
        async with ServiceRegistry(main=self) as registry:
            self.log.info(f'DCSServerBot v{BOT_VERSION}.{SUB_VERSION} starting up ...')
            self.log.info(f'- Python version {platform.python_version()} detected.')
            asyncio.create_task(registry.new("Monitoring").start())
            master = self.is_master()
            if master:
                await self.install_fonts()
                # config = registry.new("Configuration")
                # asyncio.create_task(config.start())
                bot = cast(BotService, registry.new("Bot"))
                asyncio.create_task(bot.start(token=self.config['BOT']['TOKEN']))
            else:
                els = cast(EventListenerService, registry.new("EventListener"))
                asyncio.create_task(els.start())
            if self.config['BOT'].getboolean('USE_DASHBOARD'):
                dashboard = cast(Dashboard, registry.new("Dashboard"))
                asyncio.create_task(dashboard.start())
            while True:
                # wait until the master changes
                while master == self.is_master():
                    await asyncio.sleep(1)
                # switch master
                master = not master
                if master:
                    self.log.info("Master is not responding... taking over.")
                    if self.config['BOT'].getboolean('USE_DASHBOARD'):
                        await dashboard.stop()
                    await els.stop()
                    await self.install_fonts()
                    # config = registry.new("Configuration")
                    # asyncio.create_task(config.start())
                    bot = cast(BotService, registry.new("Bot"))
                    asyncio.create_task(bot.start(token=self.config['BOT']['TOKEN']))
                else:
                    self.log.info("Second Master found, stepping back to Agent.")
                    if self.config['BOT'].getboolean('USE_DASHBOARD'):
                        await dashboard.stop()
                    # await config.stop()
                    await bot.stop()
                    els = cast(EventListenerService, registry.new("EventListener"))
                    asyncio.create_task(els.start())
                if self.config['BOT'].getboolean('USE_DASHBOARD'):
                    await dashboard.start()
