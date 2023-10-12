import aiohttp
import asyncio
import certifi
import discord
import json
import logging
import os
import platform
import psycopg
import shutil
import ssl
import subprocess
import sys
import time

from contextlib import closing
from core import utils, Status, Coalition
from core.const import SAVED_GAMES
from datetime import datetime
from discord.ext import tasks
from logging.handlers import RotatingFileHandler
from pathlib import Path
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from typing import Optional, Union, TYPE_CHECKING, Awaitable, Callable, Any, Tuple
from version import __version__

from core.autoexec import Autoexec
from core.data.dataobject import DataObjectFactory
from core.data.node import Node, UploadStatus
from core.data.instance import Instance
from core.data.impl.instanceimpl import InstanceImpl
from core.data.server import Server
from core.data.impl.serverimpl import ServerImpl
from core.services.registry import ServiceRegistry
from core.utils.dcs import LICENSES_URL
from core.utils.helper import SettingsDict

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from services import ServiceBus, BotService

__all__ = [
    "NodeImpl"
]

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
        self.node = self  # to be able to address self.node
        self.guild_id: int = int(self.config['guild_id'])
        self._public_ip: Optional[str] = None
        self.listen_address = self.config.get('listen_address', '0.0.0.0')
        self.listen_port = self.config.get('listen_port', 10042)
        self.log = self.init_logger()
        self.bot_version = __version__[:__version__.rfind('.')]
        self.sub_version = int(__version__[__version__.rfind('.') + 1:])
        self.all_nodes: Optional[dict] = None
        self.instances: list[InstanceImpl] = list()
        self.update_pending = False
        self.before_update: dict[str, Callable[[], Awaitable[Any]]] = dict()
        self.after_update: dict[str, Callable[[], Awaitable[Any]]] = dict()

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
                        exit(-2)
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
            ServiceRegistry.get("ServiceBus").send_to_node({
                "command": "rpc",
                "service": "Bot",
                "method": "audit",
                "params": {
                    "message": message,
                    "user": f"<@{user.id}>" if isinstance(user, discord.Member) else user,
                    "server": server.name if server else ""
                }
            })

    def register_callback(self, what: str, name: str, func: Callable[[], Awaitable[Any]]):
        if what == 'before_dcs_update':
            self.before_update[name] = func
        else:
            self.after_update[name] = func

    def unregister_callback(self, what: str, name: str):
        if what == 'before_dcs_update':
            del self.before_update[name]
        else:
            del self.after_update[name]

    @staticmethod
    def shutdown():
        raise KeyboardInterrupt()

    def read_locals(self) -> dict:
        _locals = dict()
        if os.path.exists('config/nodes.yaml'):
            self.all_nodes: dict = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
            node: dict = self.all_nodes.get(self.name)
            if not node:
                self.log.error(f'No configuration found for node {self.name} in nodes.yaml! Hostname changed?')
                raise KeyboardInterrupt()
            for name, element in node.items():
                if name == 'instances':
                    for _name, _element in node['instances'].items():
                        instance: InstanceImpl = DataObjectFactory().new(Instance.__name__, node=self, name=_name,
                                                                         locals=_element)
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

    async def upgrade(self) -> None:
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
                                proc = await asyncio.create_subprocess_exec(
                                    sys.executable, '-m', 'pip', '-q', 'install', '-r', 'requirements.txt')
                                await proc.communicate()
                            self.log.warning('- Restart needed => exiting.')
                            raise KeyboardInterrupt()
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

    async def get_dcs_branch_and_version(self) -> Tuple[str, str]:
        with open(os.path.join(self.installation, 'autoupdate.cfg'), encoding='utf8') as cfg:
            data = json.load(cfg)
        return data['branch'], data['version']

    async def update(self, warn_times: list[int]):
        async def shutdown_with_warning(server: Server):
            if server.is_populated():
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            server.sendPopupMessage(Coalition.ALL, f'Server is going down for a DCS update in '
                                                                   f'{utils.format_time(warn_time)}!')
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await server.shutdown()

        self.update_pending = True
        self.log.info('Shutting down local DCS servers (user warnings may apply) ...')
        servers = []
        tasks = []
        bus = ServiceRegistry.get('ServiceBus')  # type: ServiceBus
        for server in [x for x in bus.servers.values() if not x.is_remote]:
            if server.maintenance:
                servers.append(server)
            else:
                server.maintenance = True
                if server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
                    tasks.append(asyncio.create_task(shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if tasks:
            await asyncio.gather(*tasks)
        self.log.info(f"Updating {self.installation} ...")
        # call before update hooks
        for callback in self.before_update.values():
            await callback()
        # disable any popup on the remote machine
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            process = await asyncio.create_subprocess_exec(
                os.path.join(self.installation, 'bin', 'dcs_updater.exe'),
                '--quiet', 'update', startupinfo=startupinfo
            )
            await process.communicate()
            if process.returncode != 0:
                return process.returncode
        except Exception as ex:
            self.log.exception(ex)
            return -1
        if self.locals['DCS'].get('desanitize', True):
            utils.desanitize(self)
        # call after update hooks
        for callback in self.after_update.values():
            await callback()
        self.log.info(f"{self.installation} updated to the latest version. "
                      f"Starting up DCS servers again ...")
        for server in [x for x in bus.servers.values() if not x.is_remote]:
            if server not in servers:
                # let the scheduler do its job
                server.maintenance = False
            else:
                try:
                    # the server was running before (being in maintenance mode), so start it again
                    await server.startup()
                except asyncio.TimeoutError:
                    self.log.warning(f'Timeout while starting {server.display_name}, please check it manually!')
        self.update_pending = False
        self.log.info('DCS servers started (or Scheduler taking over in a bit).')
        return 0

    async def handle_module(self, what: str, module: str):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        proc = await asyncio.create_subprocess_exec(
            os.path.join(self.installation, 'bin', 'dcs_updater.exe'),
            '--quiet', what, module, startupinfo=startupinfo)
        await proc.communicate()

    async def get_installed_modules(self) -> list[str]:
        with open(os.path.join(self.installation, 'autoupdate.cfg'), encoding='utf8') as cfg:
            data = json.load(cfg)
        return data['modules']

    async def get_available_modules(self, userid: Optional[str] = None, password: Optional[str] = None) -> list[str]:
        licenses = {
            "CAUCASUS_terrain",
            "NEVADA_terrain",
            "NORMANDY_terrain",
            "PERSIANGULF_terrain",
            "THECHANNEL_terrain",
            "SYRIA_terrain",
            "MARIANAISLANDS_terrain",
            "FALKLANDS_terrain",
            "SINAIMAP_terrain",
            "WWII-ARMOUR",
            "SUPERCARRIER"
        }
        if not userid:
            return list(licenses)
        else:
            auth = aiohttp.BasicAuth(login=userid, password=password)
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where())), auth=auth) as session:
                async with session.get(LICENSES_URL) as response:
                    if response.status == 200:
                        all_licenses = (await response.text(encoding='utf8')).split('<br>')[1:]
                        for lic in all_licenses:
                            if lic.endswith('_terrain'):
                                licenses.add(lic)
            return list(licenses)

    async def register(self):
        self._public_ip = self.locals.get('public_ip')
        if not self._public_ip:
            self._public_ip = await utils.get_public_ip()
        if self.locals['DCS'].get('autoupdate', False):
            self.autoupdate.start()

    async def unregister(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM nodes WHERE guild_id = %s AND node = %s", (self.guild_id, self.name))
        if self.locals['DCS'].get('autoupdate', False):
            self.autoupdate.cancel()

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

    async def shell_command(self, cmd: str):
        self.log.debug('Running shell-command: ' + cmd)
        await asyncio.create_subprocess_shell(cmd)

    async def read_file(self, path: str) -> Union[bytes, int]:
        path = os.path.expandvars(path)
        if self.node.master:
            with open(path, mode='rb') as file:
                return file.read()
        else:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with open(path, mode='rb') as file:
                        conn.execute("INSERT INTO files (name, data) VALUES (%s, %s)",
                                     (path, psycopg.Binary(file.read())))
                    return conn.execute("SELECT currval('files_id_seq')").fetchone()[0]

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        if os.path.exists(filename) and not overwrite:
            return UploadStatus.FILE_EXISTS

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    try:
                        # make sure the directory exists
                        os.makedirs(os.path.dirname(filename), exist_ok=True)
                        with open(filename, 'wb') as outfile:
                            outfile.write(await response.read())
                    except OSError as ex:
                        self.log.exception(ex)
                        return UploadStatus.WRITE_ERROR
                else:
                    return UploadStatus.READ_ERROR
        return UploadStatus.OK

    async def list_directory(self, path: str, pattern: str) -> list[str]:
        directory = Path(os.path.expandvars(path))
        ret = []
        for file in sorted(directory.glob(pattern), key=os.path.getmtime, reverse=True):
            ret.append(os.path.join(directory.__str__(), file.name))
        return ret

    async def rename_server(self, server: Server, new_name: str, update_settings: Optional[bool] = False):
        if not self.master:
            self.log.error(f"Rename request received for server {server.name} that should have gone to the master node!")
            return
        bus: ServiceBus = ServiceRegistry.get('ServiceBus')
        old_name = server.name
        # we are doing the database changes, as we are the master
        bot: BotService = ServiceRegistry.get('Bot')
        bot.rename(server, new_name)
        # then we tell the real server to update its stuff
        await bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "server_name": server.name,
            "method": "do_rename",
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, node=server.node)
        filename = 'config/nodes.yaml'
        if os.path.exists(filename):
            data = yaml.load(Path(filename).read_text(encoding='utf-8'))
            data[server.node.name]['instances'][server.instance.name]['server'] = new_name
            with open(filename, 'w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)
        # we only need to change the name of the proxy as the server would have been renamed already otherwise
        if server.is_remote:
            server.name = new_name
        # update the local tables
        bus.servers[server.name] = server
        del bus.servers[old_name]

    @tasks.loop(minutes=5.0)
    async def autoupdate(self):
        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            branch, old_version = await self.get_dcs_branch_and_version()
            new_version = await utils.getLatestVersion(branch, userid=self.locals['DCS'].get('dcs_user'),
                                                       password=self.locals['DCS'].get('dcs_password'))
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                rc = await self.update([300, 120, 60])
                if rc == 0:
                    ServiceRegistry.get('ServiceBus').send_to_node({
                        "command": "rpc",
                        "service": "Bot",
                        "method": "audit",
                        "params": {
                            "message": f"DCS World updated to version {new_version} on node {self.node.name}."
                        }
                    })
        except Exception as ex:
            self.log.exception(ex)

    async def add_instance(self, name: str, *, template: Optional[Instance] = None) -> Instance:
        max_bot_port = -1
        max_dcs_port = -1
        max_webgui_port = -1
        for instance in self.instances:
            if instance.bot_port > max_bot_port:
                max_bot_port = instance.bot_port
            if instance.dcs_port > max_dcs_port:
                max_dcs_port = instance.dcs_port
            if instance.webgui_port > max_webgui_port:
                max_webgui_port = instance.webgui_port
        os.makedirs(os.path.join(SAVED_GAMES, name), exist_ok=True)
        instance: InstanceImpl = DataObjectFactory().new(Instance.__name__, node=self, name=name)
        instance.locals = {
            "bot_port": max_bot_port + 1,
            "dcs_port": max_dcs_port + 10,
            "webgui_port": max_webgui_port + 2
        }
        os.makedirs(os.path.join(instance.home, 'Config'), exist_ok=True)
        # should we copy from a template
        if template:
            shutil.copy2(os.path.join(template.home, 'Config', 'autoexec.cfg'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(template.home, 'Config', 'serverSettings.lua'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(template.home, 'Config', 'options.lua'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(template.home, 'Config', 'network.vault'),
                         os.path.join(instance.home, 'Config'))
            if template.extensions and template.extensions.get('SRS'):
                shutil.copy2(os.path.expandvars(template.extensions['SRS']['config']),
                             os.path.join(instance.home, 'Config', 'SRS.cfg'))
        autoexec = Autoexec(instance=instance)
        autoexec.webgui_port = instance.webgui_port
        autoexec.webrtc_port = instance.dcs_port + 1
        autoexec.crash_report_mode = "silent"
        with open('config/nodes.yaml') as infile:
            config = yaml.load(infile)
        config[platform.node()]['instances'][instance.name] = {
            "home": instance.home,
            "bot_port": instance.bot_port
        }
        with open('config/nodes.yaml', 'w') as outfile:
            yaml.dump(config, outfile)
        settings_path = os.path.join(instance.home, 'Config', 'serverSettings.lua')
        if os.path.exists(settings_path):
            settings = SettingsDict(self, settings_path, root='cfg')
            settings['port'] = instance.dcs_port
            settings['name'] = 'n/a'
        self.instances.append(instance)
        return instance

    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        with open('config/nodes.yaml') as infile:
            config = yaml.load(infile)
        del config[platform.node()]['instances'][instance.name]
        with open('config/nodes.yaml', 'w') as outfile:
            yaml.dump(config, outfile)
        self.instances.remove(instance)
        if remove_files:
            shutil.rmtree(instance.home, ignore_errors=True)

    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        with open('config/nodes.yaml') as infile:
            config = yaml.load(infile)
        new_home = os.path.join(os.path.dirname(instance.home), new_name)
        os.rename(instance.home, new_home)
        config[platform.node()]['instances'][new_name] = config[platform.node()]['instances'][instance.name].copy()
        config[platform.node()]['instances'][new_name]['home'] = new_home
        instance.name = new_name
        instance.locals['home'] = new_home
        del config[platform.node()]['instances'][instance.name]
        with open('config/nodes.yaml', 'w') as outfile:
            yaml.dump(config, outfile)

    async def find_all_instances(self) -> list[Tuple[str, str]]:
        return utils.findDCSInstances()

    async def migrate_server(self, server: Server, instance: Instance) -> None:
        await server.node.unregister_server(server)
        bus = ServiceRegistry.get("ServiceBus")
        server: ServerImpl = DataObjectFactory().new(
            Server.__name__, node=self.node, port=instance.bot_port, name=server.name)
        server.status = Status.SHUTDOWN
        bus.servers[server.name] = server
        instance.server = server
        with open('config/nodes.yaml') as infile:
            config = yaml.load(infile)
        config[self.name]['instances'][instance.name]['server'] = server.name
        with open('config/nodes.yaml', 'w') as outfile:
            yaml.dump(config, outfile)

    async def unregister_server(self, server: Server) -> None:
        instance = server.instance
        instance.server = None
        with open('config/nodes.yaml') as infile:
            config = yaml.load(infile)
        del config[self.name]['instances'][instance.name]['server']
        with open('config/nodes.yaml', 'w') as outfile:
            yaml.dump(config, outfile)
