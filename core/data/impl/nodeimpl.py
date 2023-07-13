import aiohttp
import certifi
import discord
import json
import logging
import os
import platform
import shutil
import ssl
import subprocess
import sys
import time
import yaml

from contextlib import closing
from core import utils
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from typing import Optional, Union
from version import __version__

from core.data.dataobject import DataObjectFactory
from core.data.node import Node
from core.data.instance import Instance
from core.data.impl.instanceimpl import InstanceImpl
from core.data.server import Server
from core.services.registry import ServiceRegistry
from core.utils.dcs import LICENSES_URL

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'FATAL': logging.FATAL
}


class NodeImpl(Node):

    def __init__(self):
        super().__init__(platform.node())
        self.guild_id: int = int(self.config['guild_id'])
        self._public_ip: Optional[str] = None
        self.listen_address = self.config.get('listen_address', '0.0.0.0')
        self.listen_port = self.config.get('listen_port', 10042)
        self.log = self.init_logger()
        if self.config.get('autoupdate', True):
            self.upgrade()
        self.bot_version = __version__[:__version__.rfind('.')]
        self.sub_version = int(__version__[__version__.rfind('.') + 1:])
        self.all_nodes: Optional[dict] = None
        self.log.info(f'DCSServerBot v{self.bot_version}.{self.sub_version} starting up ...')
        self.log.info(f'- Python version {platform.python_version()} detected.')
        self.db_version = None
        self.pool = self.init_db()
        self.locals: dict = self.read_locals()
        self.install_plugins()
        self.plugins: list[str] = ["mission", "scheduler", "help", "admin", "userstats", "missionstats",
                                   "creditsystem", "gamemaster", "cloud"]
        if 'opt_plugins' in self.config:
            self.plugins.extend(self.config['opt_plugins'])
        # make sure, cloud is loaded last
        if 'cloud' in self.plugins:
            self.plugins.remove('cloud')
            self.plugins.append('cloud')
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    row = conn.execute("""
                            SELECT count(*) FROM nodes 
                            WHERE guild_id = %s AND node = %s AND last_seen > (NOW() - interval '2 seconds')
                        """, (self.guild_id, self.name)).fetchone()
                    if row[0] > 0:
                        self.log.error(f"A node with name {self.name} is already running for this guild!")
                        exit(-1)
                    conn.execute("INSERT INTO nodes (guild_id, node, master) VALUES (%s, %s, False) "
                                 "ON CONFLICT (guild_id, node) DO NOTHING", (self.guild_id, self.name))
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
    def shutdown():
        exit(-1)

    def read_locals(self) -> dict:
        _locals = dict()
        if os.path.exists('config/nodes.yaml'):
            self.all_nodes: dict = yaml.safe_load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
            node: dict = self.all_nodes[self.name]
            for name, element in node.items():
                if name == 'instances':
                    for _name, _element in node['instances'].items():
                        instance: InstanceImpl = DataObjectFactory().new(Instance.__name__, node=self, name=_name)
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
        os.makedirs('logs', exist_ok=True)
        fh = RotatingFileHandler(os.path.join('logs', f'dcssb-{self.name}.log'), encoding='utf-8',
                                 maxBytes=self.config['logging']['logrotate_size'],
                                 backupCount=self.config['logging']['logrotate_count'])
        fh.setLevel(LOGLEVEL[self.config['logging']['loglevel']])
        fh.setFormatter(formatter)
        fh.doRollover()
        log.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        log.addHandler(ch)
        return log

    def init_db(self):
        db_pool = ConnectionPool(self.config['database']['url'],
                                 min_size=self.config['database']['pool_min'],
                                 max_size=self.config['database']['pool_max'])
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
                        with open('sql/tables.sql') as tables_sql:
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

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

    def upgrade(self) -> None:
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
                            if modules:
                                self.log.warning('  => requirements.txt has changed. Installing missing modules...')
                                subprocess.check_call([sys.executable, '-m', 'pip', '-q', 'install', '-r',
                                                       'requirements.txt'])
                            self.log.warning('- Restart needed => exiting.')
                            exit(-1)
                        except git.exc.GitCommandError:
                            self.log.error('  => Autoupdate failed!')
                            self.log.error('     Please revert back the changes in these files:')
                            for item in repo.index.diff(None):
                                self.log.error(f'     ./{item.a_path}')
                            return
                    else:
                        self.log.debug('- No update found for DCSServerBot.')
            except git.exc.InvalidGitRepositoryError:
                self.log.error('No git repository found. Aborting. Please use "git clone" to install DCSServerBot.')
        except ImportError:
            self.log.error('Autoupdate functionality requires "git" executable to be in the PATH.')

    async def update(self):
        # TODO move update from monitoring to here (or to Bus)
        pass

    def handle_module(self, what: str, module: str):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(['dcs_updater.exe', '--quiet', what, module], executable=os.path.expandvars(
            self.locals['DCS']['installation']) + '\\bin\\dcs_updater.exe', startupinfo=startupinfo)

    def get_installed_modules(self) -> set[str]:
        with open(os.path.join(self.locals['DCS']['installation'], 'autoupdate.cfg'), encoding='utf8') as cfg:
            data = json.load(cfg)
        return set(data['modules'])

    @staticmethod
    async def get_available_modules(userid: Optional[str] = None, password: Optional[str] = None) -> set[str]:
        licenses = {"CAUCASUS_terrain", "NEVADA_terrain", "NORMANDY_terrain", "PERSIANGULF_terrain",
                    "THECHANNEL_terrain",
                    "SYRIA_terrain", "MARIANAISLANDS_terrain", "FALKLANDS_terrain", "SINAIMAP_terrain", "WWII-ARMOUR",
                    "SUPERCARRIER"}
        if not userid:
            return licenses
        else:
            auth = aiohttp.BasicAuth(login=userid, password=password)
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where())), auth=auth) as session:
                async with session.get(LICENSES_URL) as response:
                    if response.status == 200:
                        all_licenses = (await response.text(encoding='utf8')).split('<br>')[1:]
                        for l in all_licenses:
                            if l.endswith('_terrain'):
                                licenses.add(l)
            return licenses

    async def register(self):
        self._public_ip = self.locals.get('public_ip')
        if not self._public_ip:
            self._public_ip = await utils.get_public_ip()

    async def unregister(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM nodes WHERE guild_id = %s AND node = %s", (self.guild_id, self.name))

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
                        # if we are the preferred master, take it back
                        if not master and self.locals.get('preferred_master', False):
                            master = True
                        cursor.execute('UPDATE nodes SET master = %s, last_seen = NOW() '
                                       'WHERE guild_id = %s and node = %s',
                                       (master, self.guild_id, self.name))
                    # split brain detected
                    else:
                        # we are the preferred master,
                        if self.locals.get('preferred_master', False):
                            cursor.execute('UPDATE nodes SET master = False WHERE guild_id = %s and node <> %s',
                                           (self.guild_id, self.name))
                        else:
                            self.log.warning("Split brain detected, stepping back from master.")
                            cursor.execute('UPDATE nodes SET master = False, last_seen = NOW() '
                                           'WHERE guild_id = %s and node = %s',
                                           (self.guild_id, self.name))
                            master = False
            return master

    def get_active_nodes(self) -> list[str]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    return [row[0] for row in cursor.execute("""
                        SELECT node FROM nodes 
                        WHERE guild_id = %s
                        AND master is False 
                        AND last_seen > (NOW() - interval '1 minute')
                    """, (self.guild_id, ))]
