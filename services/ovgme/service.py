import asyncio
import os
import re
import shutil
import zipfile

from contextlib import closing, suppress
from core import ServiceRegistry, Service, Server, Status, ServiceInstallationError
from filecmp import cmp
from psycopg.rows import dict_row
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from services import ServiceBus

__all__ = [
    "OvGMEService"
]


@ServiceRegistry.register("OvGME", plugin='ovgme')
class OvGMEService(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        if not os.path.exists('config/services/ovgme.yaml'):
            raise ServiceInstallationError(service='OvGME', reason="config/services/ovgme.yaml missing!")
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self._config = dict[str, dict]()

    async def start(self):
        await super().start()
        self.node.register_callback('before_dcs_update', self.name, self.before_dcs_update)
        self.node.register_callback('after_dcs_update', self.name, self.after_dcs_update)
        asyncio.create_task(self.install_packages())

    async def stop(self):
        self.node.unregister_callback('before_dcs_update', self.name)
        self.node.unregister_callback('after_dcs_update', self.name)
        await super().stop()

    async def before_dcs_update(self):
        # uninstall all RootFolder-packages
        for server_name, server in self.bus.servers.items():
            for package_name, version in self.get_installed_packages(server, 'RootFolder'):
                await self.uninstall_package(server, 'RootFolder', package_name, version)

    async def after_dcs_update(self):
        await self.install_packages()

    @staticmethod
    def is_greater(v1: str, v2: str):
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        for i in range(0, max(len(parts1), len(parts2))):
            if parts1[i] > parts2[i]:
                return True
        return False

    async def install_packages(self):
        for server_name, server in self.bus.servers.items():
            if server.is_remote:
                continue
            # wait for the servers to be registered
            while server.status == Status.UNREGISTERED:
                await asyncio.sleep(1)
            config = self.get_config(server)
            if 'packages' not in config:
                return

            for package in config['packages']:
                version = package['version'] if package['version'] != 'latest' \
                    else self.get_latest_version(package['source'], package['name'])
                installed = self.check_package(server, package['source'], package['name'])
                if (not installed or installed != version) and \
                        server.status != Status.SHUTDOWN:
                    self.log.warning(f"  - Server {server.name} needs to be shutdown to install packages.")
                    break
                maintenance = server.maintenance
                server.maintenance = True
                try:
                    if not installed:
                        if await self.install_package(server, package['source'], package['name'], version):
                            self.log.info(f"- Package {package['name']}_v{version} installed.")
                        else:
                            self.log.warning(f"- Package {package['name']}_v{version} not found!")
                    elif installed != version:
                        if self.is_greater(installed, version):
                            self.log.debug(f"- Installed package {package['name']}_v{installed} is newer than the "
                                           f"configured version. Skipping.")
                            continue
                        if not await self.uninstall_package(server, package['source'], package['name'], installed):
                            self.log.warning(f"- Package {package['name']}_v{installed} could not be uninstalled!")
                        elif not await self.install_package(server, package['source'], package['name'], version):
                            self.log.warning(f"- Package {package['name']}_v{version} could not be installed!")
                        else:
                            self.log.info(f"- Package {package['name']}_v{installed} updated to v{version}.")
                finally:
                    if maintenance:
                        server.maintenance = maintenance
                    else:
                        server.maintenance = False

    @staticmethod
    def parse_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
        if filename.endswith('.zip'):
            filename = filename[:-4]
        exp = re.compile('(?P<package>.*)_v(?P<version>.*)')
        match = exp.match(filename)
        if match:
            return match.group('package'), match.group('version')
        else:
            return None, None

    def get_installed_packages(self, server: Server, folder: str) -> list[Tuple[str, str]]:
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return [
                    (x['package_name'], x['version']) for x in cursor.execute(
                        """
                        SELECT * FROM ovgme_packages WHERE server_name = %s AND folder = %s
                        """, (server.name, folder)).fetchall()
                ]

    def get_latest_version(self, folder: str, package: str) -> str:
        config = self.get_config()
        path = os.path.expandvars(config[folder])
        available = [OvGMEService.parse_filename(x) for x in os.listdir(path) if package in x]
        max_version = None
        for _, version in available:
            if not max_version or OvGMEService.is_greater(version, max_version):
                max_version = version
        return max_version

    def check_package(self, server: Server, folder: str, package_name: str) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    'SELECT version FROM ovgme_packages WHERE server_name = %s AND package_name = %s AND folder = %s',
                    (server.name, package_name, folder))
                return cursor.fetchone()[0] if cursor.rowcount == 1 else None

    async def install_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        if server.is_remote:
            return await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "OvGME",
                "method": "install_package",
                "params": {
                    "server": server.name,
                    "folder": folder,
                    "package_name": package_name,
                    "version": version
                }
            }, node=server.node.name)

        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        os.makedirs(os.path.join(path, '.' + server.instance.name), exist_ok=True)
        target = self.node.installation if folder == 'RootFolder' else server.instance.home
        for file in os.listdir(path):
            filename = os.path.join(path, file)
            if (os.path.isfile(filename) and file == package_name + '_v' + version + '.zip') or \
                    (os.path.isdir(filename) and file == package_name + '_v' + version):
                ovgme_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
                os.makedirs(ovgme_path, exist_ok=True)
                if os.path.isfile(filename) and file == package_name + '_v' + version + '.zip':
                    with open(os.path.join(ovgme_path, 'install.log'), 'w') as log:
                        with zipfile.ZipFile(filename, 'r') as zfile:
                            for name in zfile.namelist():
                                orig = os.path.join(target, name)
                                if os.path.exists(orig) and os.path.isfile(orig):
                                    log.write(f"x {name}\n")
                                    dest = os.path.join(ovgme_path, name)
                                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                                    shutil.copy2(orig, dest)
                                else:
                                    log.write(f"w {name}\n")
                                zfile.extract(name, target)
                else:
                    with open(os.path.join(ovgme_path, 'install.log'), 'w') as log:
                        def backup(p, names) -> list[str]:
                            _dir = p[len(os.path.join(path, package_name + '_v' + version)):].lstrip(os.path.sep)
                            for name in names:
                                source = os.path.join(p, name)
                                if len(_dir):
                                    name = os.path.join(_dir, name)
                                orig = os.path.join(target, name)
                                if os.path.exists(orig) and os.path.isfile(orig) and not cmp(source, orig):
                                    log.write("x {}\n".format(name.replace('\\', '/')))
                                    dest = os.path.join(ovgme_path, name)
                                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                                    shutil.copy2(orig, dest)
                                else:
                                    log.write("w {}\n".format(name.replace('\\', '/')))
                            return []

                        shutil.copytree(filename, target, ignore=backup, dirs_exist_ok=True)
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                            INSERT INTO ovgme_packages (server_name, package_name, version, folder) 
                            VALUES (%s, %s, %s, %s)
                        """, (server.name, package_name, version, folder))
                return True
        return False

    async def uninstall_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        if server.is_remote:
            return await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "OvGME",
                "method": "uninstall_package",
                "params": {
                    "server": server.name,
                    "folder": folder,
                    "package_name": package_name,
                    "version": version
                }
            }, node=server.node.name)

        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        ovgme_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        target = self.node.installation if folder == 'RootFolder' else server.instance.home
        if not os.path.exists(os.path.join(ovgme_path, 'install.log')):
            return False
        with open(os.path.join(ovgme_path, 'install.log')) as log:
            lines = log.readlines()
            # delete has to run reverse to clean the directories
            for i in range(len(lines) - 1, 0, -1):
                filename = lines[i][2:].strip()
                file = os.path.normpath(os.path.join(target, filename))
                if lines[i].startswith('w'):
                    if os.path.isfile(file):
                        os.remove(file)
                    elif os.path.isdir(file):
                        with suppress(Exception):
                            os.removedirs(file)
                elif lines[i].startswith('x'):
                    try:
                        shutil.copy2(os.path.join(ovgme_path, filename), file)
                    except FileNotFoundError:
                        self.log.warning(f"Can't recover file {filename}, because it has been removed! "
                                         f"You might need to run a slow repair.")
        shutil.rmtree(ovgme_path)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    DELETE FROM ovgme_packages 
                    WHERE server_name = %s AND folder = %s AND package_name = %s AND version = %s
                """, (server.name, folder, package_name, version))
        return True
