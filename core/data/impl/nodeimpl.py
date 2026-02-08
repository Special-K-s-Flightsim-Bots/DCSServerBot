import aiofiles
import aiohttp
import asyncio
import certifi
import discord
import glob
import gzip
import json
import math
import os
import platform
import psutil
import psycopg
import psycopg_pool
import re
import shutil
import sqlparse
import ssl
import subprocess
import sys
import zlib

if sys.platform == 'win32':
    from core.process.win32.cpu import get_cpu_name
else:
    from core.process.linux.cpu import get_cpu_name

from collections import defaultdict
from contextlib import closing
from core import utils, Status, Port, PortType
from core.const import SAVED_GAMES
from core.data.maintenance import ServerMaintenanceManager
from core.translations import get_translation
from datetime import datetime
from discord.ext import tasks
from gzip import BadGzipFile
from migrate import migrate
from packaging.version import parse
from pathlib import Path
from psycopg import sql
from psycopg.errors import InFailedSqlTransaction, ConnectionTimeout, UniqueViolation
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool, AsyncConnectionPool
from typing import Awaitable, Callable, Any
from typing_extensions import override
from urllib.parse import urlparse, quote
from version import __version__
from zoneinfo import ZoneInfo

from core.autoexec import Autoexec
from core.data.dataobject import DataObjectFactory
from core.data.node import Node, UploadStatus, SortOrder, FatalException
from core.data.instance import Instance
from core.data.impl.instanceimpl import InstanceImpl
from core.data.server import Server
from core.data.impl.serverimpl import ServerImpl
from core.services.registry import ServiceRegistry
from core.utils.helper import YAMLError, cache_with_expiration

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()


__all__ = [
    "NodeImpl",
    "DEFAULT_PLUGINS"
]

REPO_URL = "https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/releases"
LOGIN_URL = 'https://api.digitalcombatsimulator.com/gameapi/login/'
LOGOUT_URL = 'https://api.digitalcombatsimulator.com/gameapi/logout/'
UPDATER_URL = 'https://api.digitalcombatsimulator.com/gameapi/updater/branch/{}/'
LICENSES_URL = 'https://www.digitalcombatsimulator.com/checklicenses.php'

RESTART = -1
SHUTDOWN = -2
UPDATE = -3

# Internationalisation
_ = get_translation('core')

# Default Plugins
DEFAULT_PLUGINS = [
    "mission",
    "scheduler",
    "help",
    "admin",
    "userstats",
    "missionstats",
    "monitoring",
    "gamemaster",
    "creditsystem",
    "cloud"
]


class NodeImpl(Node):

    def __init__(self, name: str, config_dir: str | None = 'config'):
        super().__init__(name, config_dir)
        self.node = self  # to be able to address self.node
        self._public_ip: str | None = None
        self.bot_version = __version__[:__version__.rfind('.')]
        self.sub_version = int(__version__[__version__.rfind('.') + 1:])
        self.is_shutdown = asyncio.Event()
        self.rc = 0
        self.dcs_branch = None
        self.all_nodes: dict[str, Node | None] = {self.name: self}
        self.suspect: dict[str, Node] = {}
        self.update_pending = False
        self.before_update: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.after_update: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.db_version = None
        self.pool: ConnectionPool | None = None
        self.apool: AsyncConnectionPool | None = None
        self.cpool: AsyncConnectionPool | None = None
        self._heartbeat_conn: psycopg.AsyncConnection | None = None
        self._master = None

    def _check_branch_version(self):
        try:
            import git

            try:
                with closing(git.Repo('.')) as repo:
                    if repo.active_branch.name == 'development':
                        self.log.info(f'- Development version detected.')
            except git.InvalidGitRepositoryError:
                if os.path.isdir('.git'):
                    self.log.warning(f'- Your installation is corrupt. Run repair.cmd.')
                else:
                    raise ImportError

        except ImportError:
            self.log.info('- ZIP installation detected.')

    async def _install_plugins(self):
        await asyncio.to_thread(self.install_plugins)
        self.plugins: list[str] = [x.lower() for x in self.config.get('plugins', DEFAULT_PLUGINS)]
        for plugin in [x.lower() for x in self.config.get('opt_plugins', [])]:
            if plugin not in self.plugins:
                self.plugins.append(plugin)
        # make sure, cloud is loaded last
        if 'cloud' in self.plugins:
            self.plugins.remove('cloud')
            self.plugins.append('cloud')

    async def _post_init(self):
        self.locals = self.read_locals()
        if 'DCS' in self.locals:
            await self.get_dcs_branch_and_version()
        await self.init_db()
        # Do we have a cluster?
        if len(self.all_nodes) > 1:
            self._master = await self.heartbeat()
            self.heartbeat_loop.start()
            self.log.info("- Starting as {} ...".format("Single / Master" if self._master else "Agent"))
        else:
            self._master = True
        if self._master:
            try:
                await self.update_db()
            except Exception as ex:
                self.log.exception(ex)
        await self.init_instances()

    async def __aenter__(self):
        if sys.platform == 'win32':
            from os import system
            system(f"title DCSServerBot v{self.bot_version}.{self.sub_version} - {self.node.name}")
        self.log.info(f'DCSServerBot v{self.bot_version}.{self.sub_version} starting up ...')

        # check GIT and branch
        self._check_branch_version()

        # check Python-version
        self.log.info(f'- Python version {platform.python_version()} detected.')

        # check UAC
        if utils.is_uac_enabled():
            self.log.warning('- You need to disable User Access Control (UAC) to allow some commands to run.')

        # install plugins
        await self._install_plugins()

        # Post-init processing
        await self._post_init()
        return self

    async def __aexit__(self, _type, _value, _traceback):
        if self.heartbeat_loop.is_running():
            self.heartbeat_loop.cancel()
        await self.close_db()

    @override
    @property
    def master(self) -> bool:
        return self._master

    @override
    @master.setter
    def master(self, value: bool):
        if self._master != value:
            self._master = value

    @override
    @property
    def public_ip(self) -> str:
        return self._public_ip

    @override
    @property
    def installation(self) -> str:
        return os.path.expandvars(self.locals['DCS']['installation'])

    @property
    def listen_address(self) -> str:
        return self.locals.get('listen_address', '127.0.0.1')

    @property
    def listen_port(self) -> Port:
        return Port(self.locals.get('listen_port', 10042), PortType.UDP)

    async def audit(self, message, *, user: discord.Member | str | None = None,
                    server: Server | None = None, **kwargs):
        from services.bot import BotService
        from services.servicebus import ServiceBus

        if self.master:
            await ServiceRegistry.get(BotService).bot.audit(message, user=user, server=server, **kwargs)
        else:
            params = {
                "message": message,
                "user": f"<@{user.id}>" if isinstance(user, discord.Member) else user,
                "server": server.name if server else ""
            } | kwargs
            await ServiceRegistry.get(ServiceBus).send_to_node({
                "command": "rpc",
                "service": BotService.__name__,
                "method": "audit",
                "params": params
            })

    def register_callback(self, what: str, name: str, func: Callable[[], Awaitable[Any]]):
        if what == 'before_dcs_update':
            self.before_update[name] = func
        else:
            self.after_update[name] = func

    def unregister_callback(self, what: str, name: str):
        if what == 'before_dcs_update':
            self.before_update.pop(name, None)
        else:
            self.after_update.pop(name, None)

    @override
    async def shutdown(self, rc: int = SHUTDOWN):
        self.rc = rc
        self.is_shutdown.set()

    @override
    async def restart(self):
        await self.shutdown(RESTART)

    @override
    def read_locals(self) -> dict:
        _locals = dict()
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        if os.path.exists(config_file):
            try:
                validation = self.node.config.get('validation', 'lazy')
                if validation in ['strict', 'lazy']:
                    schema_files = ['schemas/nodes_schema.yaml']
                    schema_files.extend([str(x) for x in Path('./extensions').rglob('*_schema.yaml')])
                    utils.validate(config_file, schema_files, raise_exception=(validation == 'strict'))
                data: dict = yaml.load(Path(config_file).read_text(encoding='utf-8'))
            except MarkedYAMLError as ex:
                raise YAMLError('config_file', ex)
            for node_name in data.keys():
                if node_name not in self.all_nodes:
                    self.all_nodes[node_name] = None
            node: dict = data.get(self.name)
            if not node:
                raise FatalException(f'No configuration found for node {self.name} in {config_file}!')
            dirty = False

            # check if we need to secure the database URL
            database_url = node.get('database', {}).get('url')
            if database_url:
                url = urlparse(database_url)
                if url.password and url.password != 'SECRET':
                    utils.set_password('database', url.password, self.config_dir)
                    port = url.port or 5432
                    node['database']['url'] = \
                        f"{url.scheme}://{url.username}:SECRET@{url.hostname}:{port}{url.path}?sslmode=prefer"
                    dirty = True
                    self.log.info("Database password found, removing it from config.")
            else:
                # if we have a cluster.pkl, rename that
                cluserdb_pkl = os.path.join(self.config_dir, '.secret', 'clusterdb.pkl')
                if os.path.exists(cluserdb_pkl):
                    database_pkl = os.path.join(self.config_dir, '.secret', 'database.pkl')
                    # remove existing target to ensure a clean move
                    if os.path.exists(database_pkl):
                        os.remove(database_pkl)
                    os.replace(cluserdb_pkl, database_pkl)

            if 'DCS' in node:
                password = node['DCS'].pop('dcs_password', node['DCS'].pop('password', None))
                if password:
                    node['DCS']['user'] = node['DCS'].pop('dcs_user', node['DCS'].get('user'))
                    utils.set_password('DCS', password, self.config_dir)
                    dirty = True
            if not 'use_upnp' in node:
                node['use_upnp'] = utils.is_upnp_available()
                dirty = True
            if dirty:
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f)
            return node
        raise FatalException(f"No {config_file} found. Exiting.")

    def get_database_urls(self):
        cpool_url = self.config.get("database", self.locals.get('database'))['url']
        lpool_url = self.locals.get("database", self.config.get('database'))['url']

        try:
            lpool_pwd = utils.get_password('database', self.config_dir)
        except ValueError:
            self.log.critical(
                "Please replace the SECRET keyword in your database URL with a password!"
            )
            exit(SHUTDOWN)

        if cpool_url != lpool_url:
            try:
                cpool_pwd = utils.get_password('clusterdb', self.config_dir)
            except ValueError:
                self.log.critical(
                    "Please replace the SECRET keyword in your database URL in main.yaml with a password!"
                )
                exit(SHUTDOWN)
        else:
            cpool_pwd = lpool_pwd

        cpool_url = cpool_url.replace('SECRET', quote(cpool_pwd) or '')
        lpool_url = lpool_url.replace('SECRET', quote(lpool_pwd) or '')
        return cpool_url, lpool_url

    async def init_db(self):
        async def check_db(url: str) -> str | None:
            max_attempts = self.locals.get("database", self.config.get('database')).get('max_retries', 10)
            for attempt in range(max_attempts + 1):
                try:
                    aconn = await psycopg.AsyncConnection.connect(url, connect_timeout=5)
                    async with aconn:
                        cursor = await aconn.execute("SHOW server_version")
                        return (await cursor.fetchone())[0]
                except ConnectionTimeout:
                    if attempt < max_attempts:
                        self.log.warning("- Database not available (yet), trying again ...")
                        continue
                    raise
            # we will never be here
            return None

        cpool_url, lpool_url = self.get_database_urls()
        version = await check_db(lpool_url)
        if lpool_url != cpool_url:
            await check_db(cpool_url)

        self.log.info(f"- Connection to PostgreSQL {version} established.")

        pool_min = self.locals.get("database", self.config.get('database')).get('pool_min', 10)
        pool_max = self.locals.get("database", self.config.get('database')).get('pool_max', 20)
        max_idle = self.locals.get("database", self.config.get('database')).get('max_idle', 10 * 60.0)
        max_waiting = self.locals.get("database", self.config.get('database')).get('max_waiting', 0)
        num_workers = pool_max // 2
        timeout = 180.0 if self.locals.get('slow_system', False) else 90.0
        self.log.debug("- Initializing database pools ...")
        self.pool = ConnectionPool(lpool_url, name="SyncPool", min_size=2, max_size=10,
                                   check=ConnectionPool.check_connection, max_idle=max_idle, timeout=timeout,
                                   open=False)
        self.pool.open()

        self.apool = AsyncConnectionPool(conninfo=lpool_url, name="AsyncPool", min_size=pool_min, max_size=pool_max,
                                         check=AsyncConnectionPool.check_connection, max_idle=max_idle, timeout=timeout,
                                         num_workers=num_workers, max_waiting=max_waiting, open=False)
        await self.apool.open()

        # initialize the cluster pool
        if urlparse(lpool_url).path != urlparse(cpool_url).path:
            self.log.info("- Federation detected.")
            # create the fast cluster pool
            self.cpool = AsyncConnectionPool(
                conninfo=cpool_url, name="ClusterPool", min_size=2, max_size=4,
                check=AsyncConnectionPool.check_connection, max_idle=max_idle, timeout=timeout, open=False)
            await self.cpool.open()
        else:
            self.cpool = self.apool

        self.log.debug("- Database pools initialized.")

    async def close_db(self):
        if self._heartbeat_conn and not self._heartbeat_conn.closed:
            try:
                await self._heartbeat_conn.close()
            except Exception as ex:
                self.log.exception(ex)
        if self.pool and not self.pool.closed:
            try:
                self.pool.close()
            except Exception as ex:
                self.log.exception(ex)
        if self.apool and not self.apool.closed:
            try:
                await self.apool.close()
            except Exception as ex:
                self.log.exception(ex)
        if not self.cpool.closed:
            try:
                await self.cpool.close()
            except Exception as ex:
                self.log.exception(ex)

    async def init_instances(self):
        grouped = defaultdict(list)
        for server_name, instance_name in utils.findDCSInstances():
            grouped[server_name].append(instance_name)
        duplicates = {
            server_name: instances
            for server_name, instances in grouped.items()
            if server_name != 'n/a' and len(instances) > 1
        }
        for server_name, instances in duplicates.items():
            self.log.warning("Duplicate server \"{}\" defined in instance {}!".format(
                server_name, ', '.join(instances)))
        # remove all (old) instances before node start to avoid duplicates
        async with self.apool.connection() as conn:
            await conn.execute("""
                DELETE FROM instances WHERE node = %s
            """, (self.name,))
        # initialize the nodes
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        dirty = False

        for _name, _element in self.locals.pop('instances', {}).items():
            try:
                instance = self.instances[_name] = DataObjectFactory().new(InstanceImpl, node=self, name=_name, locals=_element)
                # make sure the necessary config is in the nodes.yaml
                instance_config = config[self.name]['instances'][_name]
                if 'dcs_port' not in instance_config:
                    instance_config['dcs_port'] = int(instance.dcs_port)
                    dirty = True
                if 'webgui_port' not in instance_config:
                    instance_config['webgui_port'] = int(instance.webgui_port)
                    dirty = True
            except UniqueViolation:
                self.log.error(f"Instance \"{_name}\" can't be added."
                               f"There is an instance already with the same server name or bot port.")
            except Exception:
                self.log.error(f"Instance \"{_name}\" can't be added.", exc_info=True)

        if dirty:
            # save the new config
            with open(config_file, mode='w', encoding='utf-8') as outfile:
                yaml.dump(config, outfile)

    async def update_db(self):
        rc = 0
        # Initialize the cluster tables ...
        async with self.cpool.connection() as conn:
            # check if there is an old database already
            cursor = await conn.execute("""
                SELECT tablename FROM pg_catalog.pg_tables WHERE tablename IN ('cluster', 'nodes', 'files')
            """)
            tables = [x[0] async for x in cursor]
            # initial setup
            if len(tables) < 3:
                with open(os.path.join('sql', 'cluster.sql'), mode='r') as tables_sql:
                    for query in [
                        stmt.strip()
                        for stmt in sqlparse.split(tables_sql.read(), encoding='utf-8')
                        if stmt.strip()
                    ]:
                        self.log.debug(query.rstrip())
                        await conn.execute(query.rstrip())
        # initialize all other tables ...
        async with self.apool.connection() as conn:
            # check if there is an old database already
            cursor = await conn.execute("""
                SELECT tablename FROM pg_catalog.pg_tables WHERE tablename IN ('version', 'plugins')
            """)
            tables = [x[0] async for x in cursor]
            # initial setup
            if len(tables) == 0:
                self.log.info('- Creating Database ...')
                with open(os.path.join('sql', 'tables.sql'), mode='r') as tables_sql:
                    for query in [
                        stmt.strip()
                        for stmt in sqlparse.split(tables_sql.read(), encoding='utf-8')
                        if stmt.strip()
                    ]:
                        self.log.debug(query.rstrip())
                        await cursor.execute(query.rstrip())
                self.log.info('- Database created.')
            else:
                # version table missing (DB version <= 1.4)
                if 'version' not in tables:
                    await conn.execute("CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY)")
                    await conn.execute("INSERT INTO version (version) VALUES ('v1.4')")
                cursor = await conn.execute('SELECT version FROM version')
                self.db_version = (await cursor.fetchone())[0]
                while os.path.exists(f'sql/update_{self.db_version}.sql'):
                    old_version = self.db_version
                    with open(os.path.join('sql', f'update_{self.db_version}.sql'), mode='r') as tables_sql:
                        for query in [
                            stmt.strip()
                            for stmt in sqlparse.split(tables_sql.read(), encoding='utf-8')
                            if stmt.strip()
                        ]:
                            self.log.debug(query.rstrip())
                            await conn.execute(query.rstrip())
                    cursor = await conn.execute('SELECT version FROM version')
                    self.db_version = (await cursor.fetchone())[0]
                    rc = await asyncio.to_thread(migrate, self.node, old_version, self.db_version)
                    if rc != -2:
                        self.log.info(f'- Database upgraded from {old_version} to {self.db_version}.')
        if rc in [-1, -2]:
            sys.exit(rc)

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

    async def _upgrade_pending_git(self) -> bool:
        import git

        try:
            with closing(git.Repo('.')) as repo:
                current_hash = repo.head.commit.hexsha
                origin = repo.remotes.origin
                origin.fetch()
                new_hash = origin.refs[repo.active_branch.name].object.hexsha
                if new_hash != current_hash:
                    return True
        except git.InvalidGitRepositoryError:
            return await self._upgrade_pending_non_git()
        except git.GitCommandError as ex:
            self.log.error('  => Autoupdate failed!')
            changed_files = set()
            # Add staged changes
            for item in repo.index.diff(None):
                changed_files.add(item.a_path)
            # Add unstaged changes
            for item in repo.head.commit.diff(None):
                changed_files.add(item.a_path)
            if changed_files:
                self.log.error('     Please revert back the changes in these files:')
                for path in changed_files:
                    self.log.error(f'     ./{path}')
            else:
                self.log.error(ex)
        except ValueError as ex:
            self.log.error(ex)
        return False

    async def _upgrade_pending_non_git(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(REPO_URL, proxy=self.proxy, proxy_auth=self.proxy_auth) as response:
                    response.raise_for_status()
                    result = await response.json()
                    current_version = re.sub('^v', '', __version__)
                    latest_version = re.sub('^v', '', result[0]["tag_name"])

                    if parse(latest_version) > parse(current_version):
                        return True
        except aiohttp.ClientResponseError as ex:
            # ignore rate limits
            if ex.status != 403:
                raise
        return False

    @override
    async def upgrade_pending(self) -> bool:
        self.log.debug('- Checking for updates...')
        try:
            try:
                rc = await self._upgrade_pending_git()
            except ImportError:
                rc = await self._upgrade_pending_non_git()
        except Exception as ex:
            self.log.exception(ex)
            raise
        if not rc:
            self.log.debug('- No update found for DCSServerBot.')
        return rc

    async def _upgrade(self, conn: psycopg.AsyncConnection):
        # We do not want to run an upgrade if we are on a cloud drive. Just restart in this case.
        if not self.master and self.locals.get('cluster', {}).get('cloud_drive', True):
            self.log.debug("Upgrade: restart agent after master upgrade.")
            await self.restart()
        elif await self.upgrade_pending():
            if self.master:
                self.log.debug("Upgrade: set update pending to TRUE.")
                await conn.execute("""
                    UPDATE cluster SET update_pending = TRUE WHERE guild_id = %s
                """, (self.guild_id, ))
            self.log.debug("Upgrade: launch update.py")
            await self.shutdown(UPDATE)
        elif self.master:
            self.log.debug("Upgrade: reset update pending to FALSE.")
            await conn.execute("""
               UPDATE cluster
               SET update_pending = FALSE, version = %s
               WHERE guild_id = %s
           """, (__version__, self.guild_id))

    @override
    async def upgrade(self):
        async with self.cpool.connection() as conn:
            await self._upgrade(conn)

    @override
    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        if not self.dcs_branch or not self.dcs_version:
            try:
                async with aiofiles.open(os.path.join(self.installation, 'autoupdate.cfg'), mode='r', encoding='utf8') as cfg:
                    data = json.loads(await cfg.read())
                self.dcs_branch = data.get('branch', 'release')
                self.dcs_version = data['version']
                if 'DEDICATED_SERVER' in await self.get_installed_modules():
                    self.log.error("You're using the OLD dedicated server, which is deprecated.\n"
                                   "Use /dcs update to update to the release branch.")
                if "openbeta" in self.dcs_branch:
                    self.log.warning("You're running DCS OpenBeta, which is discontinued.\n"
                                     "Use /dcs update if you want to switch to the release branch.")
            except FileNotFoundError:
                self.log.critical(f"No DCS installation found at {self.installation}")
                exit(SHUTDOWN)
        return self.dcs_branch, self.dcs_version

    async def update(self, warn_times: list[int], branch: str | None = None, version: str | None = None) -> int:

        async def do_update(branch: str, version: str | None = None) -> int:
            # disable any popup on the remote machine
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
                startupinfo.wShowWindow = subprocess.SW_HIDE
                startupinfo.wShowWindow = subprocess.SW_HIDE
            else:
                startupinfo = None

            def run_subprocess() -> int:
                try:
                    # we delete the downloads dir, as we do not want to click anything
                    utils.safe_rmtree(os.path.join(self.installation, '_downloads'))
                    # then do the update
                    cmd = [os.path.join(self.installation, 'bin', 'dcs_updater.exe'), '--quiet', 'update']
                    if version:
                        cmd.append(f"{version}@{branch}")
                    else:
                        cmd.append(f"@{branch}")

                    process = subprocess.run(
                        cmd, startupinfo=startupinfo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return process.returncode
                except Exception as ex:
                    self.log.exception(ex)
                    return -1

# TODO: optional version which uses the update wrapper (and can click the Delete button)
#
#            def run_subprocess() -> int:
#                if sys.platform != 'win32':
#                    raise NotImplementedError("DCS update is not yet supported on Linux")
#
#                if utils.is_uac_enabled():
#                    raise PermissionError("You need to disable UAC to run a DCS update.")
#
#                args = [
#                    "core/utils/updater_wrapper.py",
#                    "-d", os.path.normpath(self.installation),
#                    "update"
#                ]
#                if branch:
#                    args.extend(["-b", branch])
#                if version:
#                    args.extend(["-v", version])
#                cmdline = subprocess.list2cmdline(args)
#                return utils.run_elevated(
#                    sys.executable,
#                    os.getcwd(),
#                    cmdline
#                )

            # check if there is an update running already
            proc = next(utils.find_process("DCS_updater.exe"), None)
            if proc:
                self.log.info("- DCS Update already in progress, waiting ...")
                while proc.is_running():
                    await asyncio.sleep(1)
                # assuming the update was a success
                rc = 0
            else:
                rc = await asyncio.to_thread(run_subprocess)
            if branch and rc == 0:
                # check if the branch has been changed
                config = os.path.join(self.installation, 'autoupdate.cfg')
                with open(config, mode='r') as infile:
                    data = json.load(infile)
                if data['branch'] != branch:
                    data['branch'] = branch
                    with open(config, mode='w') as outfile:
                        json.dump(data, outfile, indent=2)
            return rc

        self.update_pending = True
        async with ServerMaintenanceManager(
                self.node,
                warn_times = warn_times,
                message = _('Server is going down for a DCS update in {}!')
        ):
            self.log.info(f"Updating {self.installation} ...")
            # call before_update hooks
            for callback in self.before_update.values():
                await callback()
            try:
                old_branch, old_version = await self.get_dcs_branch_and_version()
                if not branch:
                    branch = old_branch
                if not version:
                    version = await self.get_latest_version(branch)
                rc = await do_update(branch, version)
                if rc in [0, 350]:
                    self.dcs_branch = self.dcs_version = None
                    dcs_branch, dcs_version = await self.get_dcs_branch_and_version()
                    # if only the updater updated itself, run the update again
                    if old_branch == dcs_branch and old_version == dcs_version:
                        self.log.info("dcs_updater.exe updated to the latest version, now updating DCS World ...")
                        rc = await do_update(branch, version)
                        self.dcs_branch = self.dcs_version = None
                        await self.get_dcs_branch_and_version()
                        if rc not in [0, 350]:
                            return rc
                    # Patch DCS files
                    if not self.locals['DCS'].get('cloud', False) or self.master:
                        if self.locals['DCS'].get('desanitize', True):
                            utils.desanitize(self)
                        # init profanity filter, if needed
                        if any(
                                instance.server.locals.get('profanity_filter', False)
                                for instance in self.instances.values() if instance.server
                        ):
                            utils.init_profanity_filter(self)
                    self.log.info(f"{self.installation} updated to version {dcs_version}.")
                return rc
            finally:
                # call after_update hooks
                for callback in self.after_update.values():
                    await callback()
                self.update_pending = False

    @override
    async def dcs_repair(self, warn_times: list[int] = None, slow: bool | None = False,
                         check_extra_files: bool | None = False):

        async def do_repair() -> int:
            def run_subprocess() -> int:
                if sys.platform != 'win32':
                    raise NotImplementedError("DCS repair is not yet supported on Linux")

                if utils.is_uac_enabled():
                    raise PermissionError("You need to disable UAC to run a DCS repair.")

                args = [
                    "core/utils/updater_wrapper.py",
                    "-d", os.path.normpath(self.installation),
                    "repair"
                ]
                if slow:
                    args.append("-s")
                if check_extra_files:
                    args.append("-c")
                cmdline = subprocess.list2cmdline(args)
                return utils.run_elevated(
                    sys.executable,
                    os.getcwd(),
                    cmdline
                )

            # check if there is an update / repair running already
            proc = next(utils.find_process("DCS_updater.exe"), None)
            if proc:
                self.log.info("- DCS Update / repair in progress, waiting ...")
                while proc.is_running():
                    await asyncio.sleep(1)

            return await asyncio.to_thread(run_subprocess)

        self.update_pending = True
        async with ServerMaintenanceManager(
                self.node,
                warn_times = warn_times,
                message = _('Server is going down for maintenance in {}!')
        ):
            self.log.info(f"Repairing {self.installation} ...")
            # call before_update hooks
            for callback in self.before_update.values():
                await callback()
            try:
                rc = await do_repair()
                if rc == 0:
                    # Patch DCS files
                    if not self.locals['DCS'].get('cloud', False) or self.master:
                        if self.locals['DCS'].get('desanitize', True):
                            utils.desanitize(self)
                        # init profanity filter, if needed
                        if any(
                                instance.server.locals.get('profanity_filter', False)
                                for instance in self.instances.values() if instance.server
                        ):
                            utils.init_profanity_filter(self)
                    self.log.info(f"{self.installation} repaired.")
                    message = f"DCS World repaired on node {self.node.name}."
                else:
                    self.log.error(f"Repair of {self.installation} failed with code {rc}.")
                    message = f"DCS World repair failed on node {self.node.name} with code {rc}."
            except PermissionError:
                message = f"Could not desanitise DCS World on node {self.node.name} due to permission error."
                raise
            except Exception as ex:
                message = f"DCS World repair failed on node {self.node.name} with exception {ex}."
                raise
            finally:
                from services.servicebus import ServiceBus
                from services.bot import BotService

                # call after_update hooks
                for callback in self.after_update.values():
                    await callback()
                self.update_pending = False

                bus = ServiceRegistry.get(ServiceBus)
                await bus.send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "audit",
                    "params": {
                        "message": message
                    }
                })

            return rc

    @override
    async def handle_module(self, what: str, module: str):
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
            startupinfo.wShowWindow = subprocess.SW_HIDE
        else:
            startupinfo = None

        def run_subprocess():
            subprocess.run(
                [os.path.join(self.installation, 'bin', 'dcs_updater.exe'), '--quiet', what, module],
                startupinfo=startupinfo
            )

        async with ServerMaintenanceManager(
                self.node,
                warn_times = [120, 60, 10],
                message = _('Server is going down to {what}'.format(what=what) + ' a module in {}!')
        ):
            await asyncio.to_thread(run_subprocess)

    @override
    @cache_with_expiration(expiration=60)
    async def get_installed_modules(self) -> list[str]:
        with open(os.path.join(self.installation, 'autoupdate.cfg'), mode='r', encoding='utf8') as cfg:
            data = json.load(cfg)
        return data['modules']

    @override
    @cache_with_expiration(expiration=120)
    async def get_available_modules(self) -> list[str]:
        licenses = {
            "CAUCASUS_terrain",
            "NEVADA_terrain",
            "NORMANDY_terrain",
            "PERSIANGULF_terrain",
            "THECHANNEL_terrain",
            "SYRIA_terrain",
            "MARIANAISLANDS_terrain",
            "MARIANAISLANDSWWII_terrain",
            "FALKLANDS_terrain",
            "SINAIMAP_terrain",
            "KOLA_terrain",
            "AFGHANISTAN_terrain",
            "IRAQ_terrain",
            "GERMANYCW_terrain",
            "WWII-ARMOUR",
            "SUPERCARRIER"
        }
        user = self.locals['DCS'].get('user')
        if not user:
            return list(licenses)
        password = utils.get_password('DCS', self.config_dir)
        headers = {
            'User-Agent': 'DCS_Updater/'
        }
        async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
            async with await session.post(LOGIN_URL, data={"login": user, "password": password}, proxy=self.proxy,
                                          proxy_auth=self.proxy_auth) as r1:
                if r1.status == 200:
                    async with session.get(LICENSES_URL) as r2:
                        if r2.status == 200:
                            all_licenses = (await r2.text(encoding='utf8')).split('<br>')[1:]
                            for lic in all_licenses:
                                if lic.endswith('_terrain'):
                                    licenses.add(lic)
                    async with session.get(LOGOUT_URL):
                        pass
        return list(licenses)

    @override
    @cache_with_expiration(expiration=120)
    async def get_available_dcs_versions(self, branch: str) -> list[str] | None:

        async def _get_latest_versions_no_auth() -> list[str] | None:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(
                        UPDATER_URL.format(branch), proxy=self.proxy, proxy_auth=self.proxy_auth) as response:
                    if response.status == 200:
                        return [x['version'] for x in json.loads(gzip.decompress(await response.read()))['versions2']]
            return None

        async def _get_latest_versions_auth() -> list[str] | None:
            user = self.locals['DCS'].get('user')
            password = utils.get_password('DCS', self.config_dir)
            headers = {
                'User-Agent': 'DCS_Updater/'
            }
            async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with await session.post(LOGIN_URL, data={"login": user, "password": password},
                                              proxy=self.proxy, proxy_auth=self.proxy_auth) as r1:
                    rc = None
                    if r1.status == 200:
                        async with await session.get(UPDATER_URL.format(branch)) as r2:
                            if r2.status == 200:
                                result = await r2.read()
                                try:
                                    rc = [x['version'] for x in json.loads(gzip.decompress(result))['versions2']]
                                except BadGzipFile:
                                    self.log.warning(f"ED response is not a GZIP: {result.decode('utf8')}")
                        async with await session.get(LOGOUT_URL):
                            pass
                return rc

        if not self.locals['DCS'].get('user'):
            return await _get_latest_versions_no_auth()
        else:
            return await _get_latest_versions_auth()


    @override
    async def get_latest_version(self, branch: str) -> str | None:
        versions = await self.get_available_dcs_versions(branch)
        return versions[-1] if versions else None

    async def register(self):
        self._public_ip = self.locals.get('public_ip')
        if not self._public_ip:
            self._public_ip = await utils.get_public_ip(self)
            self.log.info(f"- Public IP registered as: {self.public_ip}")
        if 'DCS' in self.locals:
            if self.locals['DCS'].get('autoupdate', False):
                if not self.locals['DCS'].get('cloud', False) or self.master:
                    self.autoupdate.start()
            else:
                new_version = await self.is_dcs_update_available()
                if new_version:
                    self.log.warning(
                        f"- Your DCS World version is outdated. Consider upgrading to version {new_version}.")

    async def unregister(self):
        async with self.cpool.connection() as conn:
            if self.master:
                cursor = await conn.execute("SELECT update_pending FROM cluster WHERE guild_id = %s",
                                            (self.guild_id,))
                row = await cursor.fetchone()
                update_pending = row and row[0]
            else:
                update_pending = False
            if not update_pending:
                await conn.execute("DELETE FROM nodes WHERE guild_id = %s AND node = %s", (self.guild_id, self.name))
        if 'DCS' in self.locals and self.locals['DCS'].get('autoupdate', False):
            if not self.locals['DCS'].get('cloud', False) or self.master:
                self.autoupdate.cancel()

    async def heartbeat(self) -> bool:
        lock_key = zlib.crc32(f"DCSSB:{self.guild_id}".encode("utf-8"))

        async def get_master_conn():
            # If we don't have a sticky connection or if it's closed, get a new one
            if not self._heartbeat_conn or self._heartbeat_conn.closed:
                self._heartbeat_conn = await psycopg.AsyncConnection.connect(
                    self.cpool.conninfo,
                    autocommit=True
                )
            return self._heartbeat_conn

        async def handle_upgrade(master: str) -> bool:
            if master == self.name:
                self.log.debug("Upgrade: Master => trigger agent upgrades")
                # let all other nodes upgrade themselve
                for node in await self.get_active_nodes():
                    data = {
                        "command": "rpc",
                        "object": "Node",
                        "method": "upgrade"
                    }
                    await conn.execute("""
                        INSERT INTO intercom (guild_id, node, data) VALUES (%s, %s, %s)
                    """, (self.guild_id, node, Json(data)))
                # clear the update flag
                await conn.execute("""
                    UPDATE cluster SET version = %s, update_pending = FALSE WHERE guild_id = %s
                """, (__version__, self.guild_id))
                return True
            elif await is_node_alive(master, 300 if self.slow_system else 180): # give the master time to upgrade
                self.log.debug("Upgrade: master => still upgrading")
                return False
            else:
                # the master is dead, so reset update pending
                self.log.error("Master died during an upgrade. Taking over ...")
                await conn.execute("UPDATE cluster SET update_pending = FALSE WHERE guild_id = %s", (self.guild_id,))
                # take over
                await take_over()
                return True

        async def get_master() -> tuple[str | None, str, bool]:
            cursor = await conn.execute("""
                SELECT master, version, update_pending
                FROM cluster
                WHERE guild_id = %s
                """, (self.guild_id,))
            row = await cursor.fetchone()
            if row is None:
                return None, __version__, False
            else:
                return row

        async def is_node_alive(node: str, timeout: int) -> bool:
            query = sql.SQL("""
                SELECT COUNT(*) FROM nodes 
                WHERE guild_id = %s AND node = %s 
                AND last_seen > (NOW() AT TIME ZONE 'UTC' - interval {interval})
            """).format(interval=sql.Literal(f"{timeout} seconds"))
            cursor = await conn.execute(query, (self.guild_id, node))
            return (await cursor.fetchone())[0] == 1

        async def take_over(block: bool = True):
            # 1. Update the table FIRST to signal intent.
            # This tells the current master to see a different name in
            # the table and call pg_advisory_unlock().
            await lock_conn.execute("""
                INSERT INTO cluster (guild_id, master, version) VALUES (%s, %s, %s)
                ON CONFLICT (guild_id) DO UPDATE 
                SET master = excluded.master, version = excluded.version
            """, (self.guild_id, self.name, __version__))
            # 2. Now wait for the physical lock to be released.
            # If another node is stepping down, this will block until they are done.
            # Try to grab the lock on the sticky connection
            if block:
                # If the master is dead, we can afford to block.
                await lock_conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
                return True
            else:
                # If master is alive (Preferred Takeover), do NOT block.
                # We try to grab the lock. If we fail, we'll try again next heartbeat.
                # This keeps our loop running so we stay 'Alive' in the nodes table.
                cursor = await lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                return (await cursor.fetchone())[0]

        async def check_nodes():
            from services.servicebus import ServiceBus

            active_nodes = set(await self.get_active_nodes())
            all_nodes = set(self.all_nodes.keys())

            # check if suspect nodes came back again
            for node_name in {name: self.suspect[name] for name in active_nodes if name in self.suspect}:
                node = self.suspect.pop(node_name)
                self.all_nodes[node.name] = node
                self.log.info(f"Node {node.name} is alive again, asking for registration ...")
                await ServiceRegistry.get(ServiceBus).register_remote_servers(node)

            # remove nodes that are no longer active
            for node_name in all_nodes - active_nodes:
                # we are never part of the active nodes list
                if node_name == self.name:
                    continue
                node = self.all_nodes[node_name]
                # remove known inactive nodes
                if not node:
                    continue
                self.log.error(f"Node {node.name} not responding.")
                await ServiceRegistry.get(ServiceBus).unregister_remote_node(node)
                self.suspect[node.name] = node

        try:
            # do not do any checks if we are supposed to shut down
            if self.node.is_shutdown.is_set():
                return self._master

            config = self.locals.get('cluster', {})
            lock_conn = await get_master_conn()

            # Check if this specific sticky session still holds the lock
            cursor = await lock_conn.execute("""
                SELECT granted FROM pg_locks
                WHERE locktype = 'advisory'
                  AND objid = %s
                  AND pid = pg_backend_pid()
            """, (lock_key, ))
            row = await cursor.fetchone()
            holds_lock = row[0] if row else False

            async with self.cpool.connection() as conn:
                try:
                    master, version, update_pending = await get_master()
                    # upgrade is pending
                    if update_pending:
                        return await handle_upgrade(master)
                    elif parse(version) > parse(__version__):
                        # avoid update loops if we are the master
                        if master == self.name:
                            self._master = True
                            self.log.warning("We are the master, but the cluster seems to have a newer version.\n"
                                             "Rolling back the cluser version to my version.")
                        await self._upgrade(conn)
                    elif parse(version) < parse(__version__) and await is_node_alive(master, config.get('heartbeat', 30)):
                        if master != self.name:
                            raise FatalException(f"This node uses DCSServerBot version {__version__} "
                                                 f"where the master uses version {version}!")
                        await self._upgrade(conn)

                    # We don't want to be a master
                    if config.get('no_master', False):
                        if not await is_node_alive(master, config.get('heartbeat', 30)):
                            raise FatalException(f"Master node {master} is not alive, exiting.")
                        return False

                    # I am the master
                    elif master == self.name:
                        if not holds_lock:
                            # Try to grab the lock on the sticky connection
                            cursor = await lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key, ))
                            if not (await cursor.fetchone())[0]:
                                self.log.warning("Conflict! Another node holds the master lock, trying next loop ...")
                                return False

                        await check_nodes()
                        return True

                    # Someone else is the master
                    elif master:
                        if holds_lock:
                            # We have the lock, but the table says someone else is master
                            await lock_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key, ))
                            return False

                        if config.get('preferred_master', False):
                            return await take_over(block=False)

                        if await is_node_alive(master, config.get('heartbeat', 30)):
                            return False

                    # No master or master is dead
                    self.log.info("No master found, taking over ...")
                    await take_over()
                    return True

                finally:
                    await conn.execute("""
                        INSERT INTO nodes (guild_id, node) VALUES (%s, %s) 
                        ON CONFLICT (guild_id, node) DO UPDATE 
                        SET last_seen = (NOW() AT TIME ZONE 'UTC')
                    """, (self.guild_id, self.name))
        except InFailedSqlTransaction:
            # we should only be here when the CLUSTER table does not exist yet
            return True

    @tasks.loop(seconds=5.0)
    async def heartbeat_loop(self):
        try:
            self._master = await self.heartbeat()
            if self.heartbeat_loop.seconds == 10.0:
                self.heartbeat_loop.change_interval(seconds=5.0)
        except psycopg_pool.PoolTimeout:
            current_stats = self.cpool.get_stats()
            self.log.warning(f"Pool stats: {repr(current_stats)}")
            if self.heartbeat_loop.seconds == 5.0:
                self.heartbeat_loop.change_interval(seconds=10.0)
            else:
                await self.restart()
        except FatalException as ex:
            self.log.critical(ex)
            exit(SHUTDOWN)
        except Exception as ex:
            self.log.exception(ex)
            await self.restart()

    async def get_active_nodes(self) -> list[str]:
        async with self.cpool.connection() as conn:
            query = sql.SQL("""
                SELECT node FROM nodes 
                WHERE guild_id = %s
                AND node <> %s
                AND last_seen > (NOW() AT TIME ZONE 'UTC' - interval {interval})
            """).format(interval=sql.Literal(f"{self.locals.get('cluster', {}).get('heartbeat', 30)} seconds"))
            cursor = await conn.execute(query, (self.guild_id, self.name))
            return [row[0] async for row in cursor]

    @override
    async def shell_command(self, cmd: str, timeout: int = 60) -> tuple[str, str] | None:
        def run_subprocess():
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return proc.communicate(timeout=timeout)

        self.log.debug('Running shell-command: ' + cmd)
        try:
            stdout, stderr = await asyncio.to_thread(run_subprocess)
            return (stdout.decode('cp1252', 'ignore') if stdout else None,
                    stderr.decode('cp1252', 'ignore') if stderr else None)
        except subprocess.TimeoutExpired:
            raise TimeoutError()

    @override
    async def read_file(self, path: str) -> bytes | int:
        async def _read_file(path: str):
            if path.startswith('http'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(path, proxy=self.proxy, proxy_auth=self.proxy_auth) as response:
                        if response.status == 200:
                            return await response.read()
                        else:
                            raise FileNotFoundError(path)
            else:
                async with aiofiles.open(path, mode='rb') as file:
                    return await file.read()

        path = os.path.expandvars(path)
        if self.node.master:
            return await _read_file(path)
        else:
            async with self.apool.connection() as conn:
                cursor = await conn.execute("""
                    INSERT INTO files (guild_id, name, data) 
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (self.guild_id, path, psycopg.Binary(await _read_file(path))))
                return (await cursor.fetchone())[0]

    @override
    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        if os.path.exists(filename) and not overwrite:
            return UploadStatus.FILE_EXISTS

        async with aiohttp.ClientSession() as session:
            async with session.get(url, proxy=self.proxy, proxy_auth=self.proxy_auth) as response:
                if response.status == 200:
                    try:
                        # make sure the directory exists
                        os.makedirs(os.path.dirname(filename), exist_ok=True)
                        async with aiofiles.open(filename, mode='wb') as outfile:
                            await outfile.write(await response.read())
                            return UploadStatus.OK
                    except Exception as ex:
                        self.log.error(ex)
                        return UploadStatus.WRITE_ERROR
                else:
                    return UploadStatus.READ_ERROR

    @override
    async def list_directory(self, path: str, *, pattern: str | list[str] = '*',
                             order: SortOrder = SortOrder.DATE,
                             is_dir: bool = False, ignore: list[str] = None, traverse: bool = False
                             ) -> tuple[str, list[str]]:
        directory = Path(os.path.expandvars(path))
        ignore = ignore or []
        ret = []
        sort_key = os.path.getmtime if order == SortOrder.DATE else str
        if isinstance(pattern, str):
            pattern = [pattern]

        def filtered_files():
            for pat in pattern:
                for file in directory.rglob(pat) if traverse else directory.glob(pat):
                    if file.name in ignore or os.path.basename(file.parent) in ignore:
                        continue
                    if (file.is_dir() and is_dir) or (not is_dir and not file.is_dir()):
                        yield file

        for file in sorted(filtered_files(), key=sort_key, reverse=sort_key != str):
            ret.append(str(file))

        return str(directory), ret

    @override
    async def create_directory(self, path: str):
        os.makedirs(path, exist_ok=True)

    @override
    async def remove_file(self, path: str):
        files = glob.glob(path)
        for file in files:
            os.remove(file)

    @override
    async def rename_file(self, old_name: str, new_name: str, *, force: bool | None = False):
        shutil.move(old_name, new_name, copy_function=shutil.copy2 if force else None)

    @override
    async def rename_server(self, server: Server, new_name: str):
        from services.servicebus import ServiceBus

        if not self.master:
            self.log.error(
                f"Rename request received for server {server.name} that should have gone to the master node!")
            return
        # update the ServiceBus
        ServiceRegistry.get(ServiceBus).rename_server(server, new_name)
        # change the proxy name for remote servers (ServerImpl will rename local ones)
        if server.is_remote:
            server.name = new_name

    async def is_dcs_update_available(self) -> str | None:
        branch, old_version = await self.get_dcs_branch_and_version()
        try:
            new_version = await self.get_latest_version(branch)
            if not new_version:
                self.log.debug("DCS update check failed, no version received from ED.")
            elif parse(old_version) < parse(new_version):
                return new_version
            elif new_version < old_version:
                self.log.warning(
                    f"Your current DCS version {old_version} has been reverted to version {new_version}."
                    f"You might want to manually downgrade the version.")
        except aiohttp.ClientError:
            self.log.warning("DCS update check failed, possible server outage at ED.")
        return None

    @override
    async def dcs_update(self, *, branch: str | None = None, version: str | None = None,
                         warn_times: list[int] = None, announce: bool | None = True):
        from services.bot import BotService
        from services.servicebus import ServiceBus

        self.log.info('A new version of DCS World is available. Auto-updating ...')
        rc = await self.update(warn_times=warn_times or [300, 120, 60], version=version, branch=branch)
        if rc == 0:
            bus = ServiceRegistry.get(ServiceBus)
            await bus.send_to_node({
                "command": "rpc",
                "service": BotService.__name__,
                "method": "audit",
                "params": {
                    "message": f"DCS World updated to version {version} on node {self.node.name}."
                }
            })
            config = self.locals.get('DCS', {}).get('announce')
            if config and announce:
                embed = discord.Embed(
                    colour=discord.Colour.blue(),
                    title=config.get(
                        'title', 'DCS has been updated to version {}!').format(version),
                    url=f"https://www.digitalcombatsimulator.com/en/news/changelog/stable/{version}/")
                embed.description = config.get('description', 'The following servers have been updated:')
                embed.set_thumbnail(url="https://forum.dcs.world/uploads/monthly_2023_10/"
                                        "icons_4.png.f3290f2c17710d5ab3d0ec5f1bf99064.png")
                embed.add_field(name=_('Server'),
                                value='\n'.join([
                                    f'- {x.display_name}' for x in bus.servers.values() if not x.is_remote
                                ]), inline=False)
                embed.set_footer(
                    text=config.get('footer', 'Please make sure you update your DCS client to join!'))
                params = {
                    "channel": config['channel'],
                    "embed": embed.to_dict()
                }
                if 'mention' in config:
                    params['mention'] = config['mention']
                await bus.send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "send_message",
                    "params": params
                })
        else:
            if rc == 2:
                message = f"DCS World update on node {self.name} was aborted (check disk space)!"
            elif rc in [3, 350]:
                message = (f"DCS World has been updated to version {version} on node {self.name}.\n"
                           f"The updater has requested a **reboot** of the system!")
            else:
                message = (f"DCS World could not be updated on node {self.name} due to an error ({rc}): "
                           f"{utils.get_win32_error_message(rc)}!")
            self.log.error(message)
            await ServiceRegistry.get(ServiceBus).send_to_node({
                "command": "rpc",
                "service": BotService.__name__,
                "method": "alert",
                "params": {
                    "title": "DCS Update Issue",
                    "message": message
                }
            })
        return rc

    def can_update(self):
        update_window = self.locals.get('DCS', {}).get('update_window')
        if update_window:
            now: datetime = datetime.now()
            tz = now.astimezone().tzinfo
            now = now.replace(tzinfo=tz)
            weekday = now.weekday()
            for period, daystate in update_window.items():  # type: str, str
                if period == 'timezone':
                    tz = ZoneInfo(daystate)
                    continue
                if len(daystate) != 7:
                    self.log.error(f"Error in nodes.yaml (update_window): {daystate} has to be 7 characters long!")
                    return False
                state = daystate[weekday]
                if utils.is_in_timeframe(now, period, tz):
                    return state.upper() == 'Y'
        return True

    @tasks.loop(minutes=5.0)
    async def autoupdate(self):
        # don't run if an update is currently running
        if self.update_pending or not self.can_update():
            return
        # Check if we are in the correct time window
        try:
            version = await self.is_dcs_update_available()
            if version:
                await self.dcs_update(version=version)

        except aiohttp.ClientError as ex:
            self.log.warning(ex)
        except Exception as ex:
            self.log.exception(ex)

    @autoupdate.before_loop
    async def before_autoupdate(self):
        from services.servicebus import ServiceBus

        # make sure the Scheduler does not interact
        self.update_pending = True
        # wait for all servers to be in a proper state
        while True:
            bus = ServiceRegistry.get(ServiceBus)
            if bus and bus.servers and all(server.status != Status.UNREGISTERED for server in bus.servers.values()):
                break
            await asyncio.sleep(1)

        # check for updates
        new_version = await self.is_dcs_update_available()
        if new_version and self.can_update():
            await self.dcs_update(version=new_version)
        self.update_pending = False

    @override
    async def add_instance(self, name: str, *, template: str = "") -> "Instance":
        from services.servicebus import ServiceBus

        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)

        max_bot_port = max_dcs_port = max_webgui_port = -1
        for node_name, node in self.all_nodes.items():
            if node:
                # the node is active
                for instance in node.instances.values():
                    if int(instance.bot_port) > max_bot_port:
                        max_bot_port = int(instance.bot_port)
                    if int(instance.dcs_port) > max_dcs_port:
                        max_dcs_port = int(instance.dcs_port)
                    if int(instance.webgui_port) > max_webgui_port:
                        max_webgui_port = int(instance.webgui_port)
            else:
                # the node is not active
                node = config.get(node_name)
                for instance in node.get('instances', {}).values():
                    if int(instance['bot_port']) > max_bot_port:
                        max_bot_port = int(instance['bot_port'])
                    if 'dcs_port' in instance and int(instance['dcs_port']) > max_dcs_port:
                        max_dcs_port = int(instance.dcs_port)
                    if 'webgui_port' in instance and int(instance['webgui_port']) > max_webgui_port:
                        max_webgui_port = int(instance['webgui_port'])

        os.makedirs(os.path.join(SAVED_GAMES, name), exist_ok=True)
        instance = DataObjectFactory().new(InstanceImpl, node=self, name=name, locals={
            "bot_port": max_bot_port + 1 if max_bot_port != -1 else 6666,
            "dcs_port": max_dcs_port + 10 if max_dcs_port != -1 else 10308,
            "webgui_port": max_webgui_port + 2 if max_webgui_port != -1 else 8088
        })
        os.makedirs(os.path.join(instance.home, 'Config'), exist_ok=True)
        if template:
            # copy from a template
            _template = self.node.instances[template]
            files = [
                os.path.join(_template.home, 'Config', 'autoexec.cfg'),
                os.path.join(_template.home, 'Config', 'serverSettings.lua'),
                os.path.join(_template.home, 'Config', 'options.lua'),
                os.path.join(_template.home, 'Config', 'network.vault')
            ]
            # add SRS configuration
            if _template.extensions and _template.extensions.get('SRS'):
                srs_config = os.path.expandvars(_template.extensions['SRS']['config'])
                if os.path.basename(srs_config) == 'SRS.cfg':
                    files.append(os.path.expandvars(_template.extensions['SRS']['config']))
                else:
                    self.log.warnig("add_instance: SRS configuration not named SRS.cfg, skipping.")
            for file in files:
                if os.path.exists(file):
                    shutil.copy2(file, os.path.join(instance.home, 'Config'))
        else:
            files = [
                os.path.join(self.config_dir, 'autoexec.cfg'),
                os.path.join(self.config_dir, 'serverSettings.lua'),
                os.path.join(self.config_dir, 'options.lua')
            ]
            # copy default files if they exist
            for file in files:
                if os.path.exists(file):
                    shutil.copy2(file, os.path.join(instance.home, 'Config'))

        autoexec = Autoexec(instance=instance)
        autoexec.crash_report_mode = "silent"
        if not self.locals.get('use_upnp', True):
            net = autoexec.net or {}
            net |= {
                "use_upnp": False
            }
            autoexec.net = net

        if 'instances' not in config[self.name]:
            config[self.name]['instances'] = {}
        config[self.name]['instances'][instance.name] = {
            "home": instance.home,
            "bot_port": int(instance.bot_port)
        }

        # save the new config
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)

        bus = ServiceRegistry.get(ServiceBus)
        server = DataObjectFactory().new(ServerImpl, node=self.node, port=instance.bot_port, name='n/a', bus=bus)
        instance.server = server
        # we cannot use instance.dcs_port here as that would refer to the assigned serverSettings.lua already
        server.settings["port"] = int(instance.locals['dcs_port'])
        self.instances[instance.name] = instance
        bus.servers[server.name] = server
        if not self.master:
            await bus.send_init(server)
        return instance

    @override
    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        config[self.name]['instances'].pop(instance.name, None)
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)
        if instance.server:
            await self.unregister_server(instance.server)
        self.instances.pop(instance.name, None)
        async with self.apool.connection() as conn:
            await conn.execute("DELETE FROM instances WHERE instance = %s", (instance.name, ))
        if remove_files:
            shutil.rmtree(instance.home, ignore_errors=True)

    @override
    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        from services.bot import BotService

        def change_instance_in_config(data: dict):
            if self.node.name in data and instance.name in data[self.node.name]:
                data[self.node.name][new_name] = data[self.node.name].pop(instance.name)
            elif instance.name in data:
                data[new_name] = data.pop(instance.name)

        def rename_path(data) -> str:
            # Only replace if the string matches the path
            if os.path.exists(os.path.expandvars(data)):
                parts = Path(data).parts
                updated_parts = [new_name if part == instance.name else part for part in parts]
                return str(Path(*updated_parts))
            return data

        def change_instance_in_path(data):
            if isinstance(data, dict):
                for key, value in data.items():
                    data[key] = change_instance_in_path(value)
            elif isinstance(data, list):
                for index, item in enumerate(data):
                    data[index] = change_instance_in_path(item)
            elif isinstance(data, str):
                return rename_path(data)
            return data

        # disable autoscan
        if instance.server and instance.server.locals.get('autoscan', False):
            await asyncio.to_thread(instance.server.stop_observer)

        try:
            # test-rename it to make sure it works
            new_home = os.path.join(os.path.dirname(instance.home), new_name)
            os.rename(instance.home, new_home)
            os.rename(new_home, instance.home)

            # change the database
            async with self.apool.connection() as conn:
                await conn.execute("""
                    UPDATE instances SET instance = %s 
                    WHERE node = %s AND instance = %s
                """, (new_name, instance.node.name, instance.name, ))

            # rename missions in the missionlist
            if instance.server:
                missions_list = instance.server.settings['missionList']
                new_mission_list = []
                for mission in missions_list:
                    new_mission_list.append(rename_path(mission))
                instance.server.settings['missionList'] = new_mission_list

            # read nodes.yaml
            config_file = os.path.join(self.config_dir, 'nodes.yaml')
            with open(config_file, mode='r', encoding='utf-8') as infile:
                config = yaml.load(infile)

            # rename the instance in nodes.yaml
            config[self.name]['instances'][new_name] = config[self.name]['instances'].pop(instance.name)
            config[self.name]['instances'][new_name]['home'] = new_home
            missions_dir = config[self.name]['instances'][new_name].get('missions_dir')
            if missions_dir:
                instance.missions_dir = config[self.name]['instances'][new_name]['missions_dir'] = rename_path(missions_dir)
            else:
                instance.missions_dir = rename_path(instance.missions_dir)

            # rename extensions in nodes.yaml
            for name, extension in config[self.name]['instances'][new_name].get('extensions', {}).items():
                change_instance_in_path(extension)

            # rename plugin configs
            for plugin in Path(os.path.join(self.config_dir, 'plugins')).glob('*.yaml'):
                data = yaml.load(plugin.read_text(encoding='utf-8'))
                change_instance_in_config(data)
                with plugin.open('w', encoding='utf-8') as outfile:
                    yaml.dump(data, outfile)

            # rename service configs
            for service in Path(os.path.join(self.config_dir, 'services')).glob('*.yaml'):
                data = yaml.load(service.read_text(encoding='utf-8'))
                change_instance_in_config(data)
                with service.open('w', encoding='utf-8') as outfile:
                    yaml.dump(data, outfile)

            # restart all services but the bot
            tasks = []
            for cls in ServiceRegistry.services().keys():
                service = ServiceRegistry.get(cls)
                if service and not isinstance(service, BotService):
                    assert service is not None
                    tasks.append(service.stop())
            await utils.run_parallel_nofail(*tasks)

            # rename the directory
            os.rename(instance.home, new_home)
            # rename the instance
            old_name = instance.name
            instance.name = new_name
            instance.locals['home'] = new_home
            self.instances[new_name] = self.instances.pop(old_name)

            # write the new nodes.yaml
            with open(config_file, mode='w', encoding='utf-8') as outfile:
                yaml.dump(config, outfile)

            # reload the bot service
            if self.master:
                bot = ServiceRegistry.get(BotService).bot
                await bot.reload()

            # and start all the rest up again
            tasks = []
            for cls in ServiceRegistry.services().keys():
                service = ServiceRegistry.get(cls)
                if service and not isinstance(service, BotService):
                    assert service is not None
                    service.reload()
                    tasks.append(service.start())
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.log.exception(f"Failed to start service {list(ServiceRegistry.services().keys())[i]}")
        finally:
            # re-init the attached server instance
            if instance.server:
                await instance.server.reload()

    @override
    async def find_all_instances(self) -> list[tuple[str, str]]:
        return utils.findDCSInstances()

    @override
    async def migrate_server(self, server: Server, instance: Instance) -> None:
        from services.servicebus import ServiceBus

        await server.node.unregister_server(server)
        bus = ServiceRegistry.get(ServiceBus)
        server = DataObjectFactory().new(ServerImpl, node=self.node, port=instance.bot_port, name=server.name, bus=bus)
        instance.server = server
        bus.servers[server.name] = server
        if not self.master:
            await bus.send_init(server)
        server.status = Status.SHUTDOWN

    @override
    async def unregister_server(self, server: Server) -> None:
        from services.servicebus import ServiceBus

        instance = server.instance
        instance.server = None
        ServiceRegistry.get(ServiceBus).servers.pop(server.name)

    @override
    async def install_plugin(self, plugin: str) -> bool:
        from services.bot import BotService
        from services.servicebus import ServiceBus

        if not self.master or plugin in self.plugins:
            return False

        # amend the main.yaml
        main_yaml = os.path.join(self.config_dir, 'main.yaml')
        data: dict = yaml.load(Path(main_yaml).read_text(encoding='utf-8'))
        if 'opt_plugins' not in data:
            data['opt_plugins'] = []
        data['opt_plugins'].append(plugin)
        with Path(main_yaml).open("w", encoding="utf-8") as file:
            yaml.dump(data, file)
        self.plugins.append(plugin)

        # install the plugin into all DCS servers
        if os.path.exists(os.path.join('plugins', plugin, 'lua')):
            for server in ServiceRegistry.get(ServiceBus).servers.values():
                await server.install_plugin(plugin)

        # load the plugin
        await ServiceRegistry.get(BotService).bot.load_plugin(plugin)
        return True

    @override
    async def uninstall_plugin(self, plugin: str) -> bool:
        from services.bot import BotService

        if not self.master or plugin not in self.plugins:
            return False
        main_yaml = os.path.join(self.config_dir, 'main.yaml')
        data: dict = yaml.load(Path(main_yaml).read_text(encoding='utf-8'))
        data['opt_plugins'].remove(plugin)
        with Path(main_yaml).open("w", encoding="utf-8") as file:
            yaml.dump(data, file)
        bot = ServiceRegistry.get(BotService).bot
        await bot.unload_plugin(plugin)
        self.plugins.remove(plugin)
        return True

    @override
    async def get_cpu_info(self, used: bool = True) -> bytes | int:
        from core.process import (ProcessManager, create_cpu_topology_visualization, get_cpus_from_affinity,
                                  get_cache_info, get_p_core_affinity, get_e_core_affinity)

        def create_image(used: bool) -> bytes:
            if used:
                return ProcessManager().visualize_usage()
            else:
                p_core_affinity_mask = get_p_core_affinity()
                e_core_affinity_mask = get_e_core_affinity()
                buffer = create_cpu_topology_visualization(get_cpus_from_affinity(p_core_affinity_mask),
                                                           get_cpus_from_affinity(e_core_affinity_mask),
                                                           get_cache_info())
                try:
                    return buffer.getvalue()
                finally:
                    buffer.close()

        if self.node.master:
            return create_image(used)
        else:
            async with self.apool.connection() as conn:
                cursor = await conn.execute("""
                    INSERT INTO files (guild_id, name, data) 
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (self.guild_id, 'cpuinfo', psycopg.Binary(create_image(used))))
                return (await cursor.fetchone())[0]

    @override
    async def info(self) -> dict:
        cpool_url, lpool_url = self.get_database_urls()

        node_dict = {
            "Node": self.node.name,
            "Listen Port": repr(self.node.listen_port),
            "Public IP": self.node.public_ip,
            "Bot Version": f"{self.node.bot_version}.{self.node.sub_version}",
            "DCS Branch": self.dcs_branch,
            "DCS Version": self.dcs_version,
            "OS": platform.system(),
            "OS Release": platform.version(),
            "CPU Name": get_cpu_name(),
            "CPU Count": psutil.cpu_count(),
            "Total RAM": math.ceil(psutil.virtual_memory().total / (1024 * 1024 * 1024)),
            "Python Version": platform.python_version()
        }
        if cpool_url != lpool_url:
            node_dict.update({
                "Cluster Database": cpool_url,
            })
        node_dict.update({
            "Database URL": lpool_url
        })
        # all services ports
        for service_class in ServiceRegistry.services().values():
            service = ServiceRegistry.get(service_class)
            if not service:
                continue
            for key, value in service.get_ports().items():
                node_dict[key] = repr(value)

        return node_dict
