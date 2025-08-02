import aiofiles
import asyncio
import os
import re
import shutil
import zipfile

from aiohttp import ClientSession, ClientResponseError
from contextlib import suppress
from core import ServiceRegistry, Service, Server, Status, ServiceInstallationError, utils, proxy, Node
from enum import Enum
from filecmp import cmp
from functools import total_ordering
from packaging import version
from pathlib import Path
from psycopg.rows import dict_row
from typing import Optional, Union
from urllib.parse import urlparse

from ..servicebus import ServiceBus

__all__ = [
    "Folder",
    "ModManagerService"
]

import sys
if sys.platform == 'win32':
    ENCODING = 'cp1252'
else:
    ENCODING = 'utf-8'

@total_ordering
class Folder(Enum):
    RootFolder = 'RootFolder'
    SavedGames = 'SavedGames'

    def __lt__(self, other):
        if isinstance(other, Folder):
            return self.value < other.value
        return NotImplemented


@ServiceRegistry.register(plugin='modmanager', depends_on=[ServiceBus])
class ModManagerService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="ModManager")
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'modmanager.yaml')):
            raise ServiceInstallationError(service='ModManager', reason="config/services/modmanager.yaml missing!")
        self.bus = ServiceRegistry.get(ServiceBus)
        self.temp_packages = []

    async def start(self):
        await super().start()
        config = self.get_config()
        for folder in Folder:
            os.makedirs(os.path.expandvars(config[folder.value]), exist_ok=True)
        self.node.register_callback('before_dcs_update', self.name, self.before_dcs_update)
        self.node.register_callback('after_dcs_update', self.name, self.after_dcs_update)
        asyncio.create_task(self.install_packages())

    async def stop(self):
        self.node.unregister_callback('before_dcs_update', self.name)
        self.node.unregister_callback('after_dcs_update', self.name)
        await super().stop()

    async def before_dcs_update(self):
        # uninstall all RootFolder-packages
        self.log.debug("  => Uninstalling any Mods from the DCS installation folder ...")
        for package_name, _version in await self.get_installed_packages(self.node, Folder.RootFolder):
            await self.uninstall_root_package(self.node, package_name, _version)

    async def after_dcs_update(self):
        self.log.debug("  => Re-installing any Mods into the DCS installation folder ...")
        await self.install_packages()

    async def install_packages(self):
        for server_name, server in self.bus.servers.items():
            if server.is_remote:
                continue
            # wait for the servers to be registered
            while server.status == Status.UNREGISTERED:
                await asyncio.sleep(1)
            config = self.get_config(server)
            if 'packages' not in config:
                continue

            for package in config.get('packages', []):
                folder = Folder(package['source'])
                if package.get('version', 'latest') == 'latest':
                    _version = await self.get_latest_version(package)
                else:
                    _version = package['version']
                installed = await self.get_installed_package(server, folder, package['name'])
                if (not installed or installed != _version) and \
                        server.status != Status.SHUTDOWN:
                    self.log.warning(
                        f"  - Server {server.name} needs to be shutdown to auto-install package {package['name']}")
                    break
                maintenance = server.maintenance
                server.maintenance = True
                try:
                    if not installed:
                        if not await self.install_package(server, folder, package['name'], _version,
                                                          package.get('repo')):
                            self.log.warning(f"- Package {package['name']}_v{_version} not found!")
                    elif installed != _version:
                        if version.parse(installed) > version.parse(_version):
                            self.log.debug(f"- Installed package {package['name']}_v{installed} is newer than the "
                                           f"configured version. Skipping.")
                            continue
                        if not await self.uninstall_package(server, folder, package['name'], installed):
                            self.log.warning(f"- Package {package['name']}_v{installed} could not be uninstalled on "
                                             f"server {server.name}!")
                        elif not await self.install_package(server, folder, package['name'], _version,
                                                            package.get('repo')):
                            self.log.warning(f"- Package {package['name']}_v{_version} could not be installed on "
                                             f"server {server.name}!")
                        else:
                            self.log.info(f"- Package {package['name']}_v{installed} updated to v{_version}.")
                finally:
                    if not maintenance:
                        server.maintenance = False

    @staticmethod
    def parse_filename(filename: str) -> tuple[Optional[str], Optional[str]]:
        if filename.endswith('.zip'):
            filename = filename[:-4]
        exp = re.compile(r'(?P<package>.*?)(?:v)?(?P<version>[0-9]+(?:\.[A-Za-z0-9._-]+)?)$')
        match = exp.match(filename)
        if match:
            return match.group('package').rstrip('v').rstrip('_').rstrip('-').strip(), match.group('version').strip()
        else:
            return None, None

    async def get_installed_packages(self, reference: Union[Server, Node], folder: Folder) -> list[tuple[str, str]]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                        SELECT * FROM mm_packages 
                        WHERE server_name = %s AND folder = %s 
                        ORDER BY package_name, version
                    """, (reference.name, folder.value))
                return [
                    (x['package_name'], x['version']) async for x in cursor
                ]

    async def get_repo_versions(self, repo: str) -> set[str]:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
        return set([x['tag_name'].strip('v') for x in data])

    async def get_download_url(self, repo: str, package_name: str, version: str) -> Optional[str]:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
        element = next((x for x in data if x['tag_name'].strip('v') == version), None)
        if not element:
            return None
        for asset in element.get('assets', []):
            if asset['name'] == f"{package_name}_{version}.zip":
                return asset['browser_download_url']
        else:
            try:
                return element['assets'][0]['browser_download_url']
            except (KeyError, IndexError):
                return None

    async def get_available_versions(self, server: Server, folder: Folder, package_name: str) -> list[str]:
        local_versions: set[str] = set()
        config = self.get_config(server)
        for x in Path(os.path.expandvars(config[folder.value])).glob(f"{package_name}*"):
            name, version = self.parse_filename(x.name)
            local_versions.add(version)
        remote_versions: set[str] = set()
        with suppress(StopIteration):
            package = next(x for x in config.get('packages', []) if x['name'] == package_name and x['source'] == folder.value)
            if 'repo' in package:
                remote_versions = {x for x in await self.get_repo_versions(package['repo'])}
        return sorted(local_versions | remote_versions)

    @staticmethod
    def extract_repo_name(url: str) -> str:
        path = urlparse(url).path
        return path.lstrip('/')

    async def download(self, url: str, folder: Folder, force: Optional[bool] = False) -> None:
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        filename = url.split('/')[-1]
        self.log.info(f"  => ModManager: Downloading {folder.value}/{filename} ...")
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                outpath = os.path.join(path, filename)
                if os.path.exists(outpath) and not force:
                    self.log.warning(f"  => ModManager: File {folder.value}/{filename} exists!")
                    raise FileExistsError(outpath)
                with open(outpath, mode='wb') as outfile:
                    outfile.write(await response.read())
        self.log.info(f"  => ModManager: {folder.value}/{filename} downloaded.")

    async def download_from_repo(self, repo: str, folder: Folder, *, package_name: Optional[str] = None,
                                 version: Optional[str] = None, force: Optional[bool] = False):
        if not package_name:
            package_name = self.extract_repo_name(repo).split('/')[-1]
        if not version or version == 'latest':
            version = await self.get_latest_repo_version(repo)
        url = await self.get_download_url(repo, package_name, version)
        try:
            await self.download(url, folder, force)
        except ClientResponseError:
            url = f'{repo}/releases/download/v{version}/{package_name}_{version}.zip'
            await self.download(url, folder, force)

    async def get_latest_repo_version(self, repo: str) -> str:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases/latest"

        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get('tag_name', '').strip('v')

    async def _get_latest_file_version(self, package: dict):
        config = self.get_config()
        source = package['source']
        if isinstance(source, Folder):
            source = source.value
        path = os.path.expandvars(config[source])
        available = [self.parse_filename(x) for x in os.listdir(path) if package['name'] in x]
        max_version = None
        for _, _version in available:
            if not max_version or version.parse(_version) > version.parse(max_version):
                max_version = _version
        return max_version.strip('v') if max_version else None

    async def get_latest_version(self, package: dict) -> str:
        if 'repo' in package:
            try:
                return await self.get_latest_repo_version(package['repo'])
            except ClientResponseError as ex:
                self.log.warning(
                    f"Can't connect to {package['repo']}: {ex.message}. "
                    f"Update-check skipped for package {package['name']}.")
        return await self._get_latest_file_version(package)

    async def get_installed_package(self, reference: Union[Server, Node], folder: Folder, package_name: str) -> Optional[str]:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT version FROM mm_packages WHERE server_name = %s AND package_name = %s AND folder = %s
            """, (reference.name, package_name, folder.value))
            return (await cursor.fetchone())[0] if cursor.rowcount == 1 else None

    async def recreate_install_log(self, reference: Union[Server, Node], folder: Folder, package_name: str, version: str) -> bool:
        config = self.get_config()
        path = os.path.expandvars(config['SavedGames'])
        if folder == Folder.SavedGames:
            packages_path = os.path.join(path, '.' + reference.instance.name, package_name + '_v' + version)
        else:
            packages_path = os.path.join(path, '.' + reference.name, package_name + '_v' + version)
        os.makedirs(packages_path, exist_ok=True)
        log_entries = []

        def recreate_normal_package():
            for root, dirs, files in os.walk(package):
                for _dir in dirs:
                    log_entries.append("w {}\n".format(os.path.relpath(os.path.join(root, _dir),
                                                                       package).replace('\\', '/')))
                for file in files:
                    log_entries.append("w {}\n".format(os.path.relpath(os.path.join(root, file),
                                                                       package).replace('\\', '/')))

        def recreate_zip_package():
            with zipfile.ZipFile(package + '.zip', 'r') as zfile:
                for name in zfile.namelist():
                    log_entries.append(f"w {name}\n")

        package = os.path.join(path, f"{package_name}_v{version}")
        if os.path.isdir(package):
            await asyncio.to_thread(recreate_normal_package)
        elif os.path.exists(package + '.zip'):
            await asyncio.to_thread(recreate_zip_package)
        else:
            return False

        async with aiofiles.open(os.path.join(packages_path, 'install.log'), 'w', encoding=ENCODING,
                                 errors='replace') as log:
            await log.writelines(log_entries)
        return True

    @staticmethod
    def is_ovgme(zfile: zipfile.ZipFile, package_name: str) -> bool:
        for zip_path in zfile.namelist():
            parts = zip_path.split('/')
            if (len(parts) >= 2 and package_name in parts[0] and
                    parts[1].lower() in ['mods', 'scripts', 'kneeboard', 'liveries']):
                return True
        return False

    async def do_install(self, reference: Union[Server, Node], folder: Folder, package_name: str, version: str,
                         path: str, filename: str) -> bool:
        if folder == Folder.SavedGames:
            target = reference.instance.home
            packages_path = os.path.join(path, '.' + reference.instance.name, package_name + '_v' + version)
        else:
            target = reference.installation
            packages_path = os.path.join(path, '.' + reference.name, package_name + '_v' + version)
        os.makedirs(packages_path, exist_ok=True)
        log_entries = []

        def process_zipfile():
            with zipfile.ZipFile(filename, 'r') as zfile:
                ovgme = self.is_ovgme(zfile, package_name)
                if ovgme:
                    root = (zfile.namelist()[0]).split('/')[0] + '/'
                for name in zfile.namelist():
                    if ovgme:
                        _name = name.replace(root, '')
                        if not _name or name in ['README.txt', 'VERSION.txt']:
                            continue
                    else:
                        _name = name
                    # ignore all Thumbs.db files
                    if os.path.basename(_name).lower() == 'thumbs.db':
                        continue
                    orig = os.path.join(target, _name)
                    if os.path.exists(orig) and os.path.isfile(orig):
                        log_entries.append(f"x {_name}\n")
                        dest = os.path.join(packages_path, _name)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(orig, dest)
                    else:
                        log_entries.append(f"w {_name}\n")
                    os.makedirs(os.path.dirname(orig), exist_ok=True)
                    if orig.endswith('/'):
                        continue
                    with zfile.open(name) as infile:
                        self.log.debug(f"Extracting file {name} to {target}/{_name}")
                        with open(orig, mode='wb') as outfile:
                            outfile.write(infile.read())
            return log_entries

        def copy_tree():
            def backup(p, names) -> list[str]:
                ignore_list = []
                _dir = p[len(os.path.join(path, package_name + '_v' + version)):].lstrip(os.path.sep)
                for name in names:
                    # ignore all Thumbs.db files
                    if name.lower() == 'thumbs.db':
                        ignore_list.append(name)
                        continue
                    source = os.path.join(p, name)
                    if len(_dir):
                        name = os.path.join(_dir, name)
                    orig = os.path.join(target, name)
                    if os.path.exists(orig) and os.path.isfile(orig) and not cmp(source, orig):
                        log_entries.append("x {}\n".format(name.replace('\\', '/')))
                        dest = os.path.join(packages_path, name)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(orig, dest)
                    else:
                        log_entries.append("w {}\n".format(name.replace('\\', '/')))
                return ignore_list

            shutil.copytree(filename, target, ignore=backup, dirs_exist_ok=True)

        if os.path.isfile(filename) and filename.endswith(".zip"):
            await asyncio.to_thread(process_zipfile)
        elif os.path.isdir(filename):
            await asyncio.to_thread(copy_tree)
        else:
            self.log.error(f"- Installation of package {package_name}_v{version} failed, no package.")
            return False
        async with aiofiles.open(os.path.join(packages_path, 'install.log'), mode='w', encoding=ENCODING,
                                 errors='replace') as log:
            await log.writelines(log_entries)

        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO mm_packages (server_name, package_name, version, folder) 
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT (server_name, package_name) 
                    DO UPDATE SET version=excluded.version
                """, (reference.name, package_name, version, folder.value))
        self.log.info(f"- Package {package_name}_v{version} successfully installed in {target}.")
        return True

    @proxy
    async def install_package(self, server: Server, folder: Union[Folder, str], package_name: str, version: str,
                              repo: Optional[str] = None) -> bool:
        self.log.info(f"Installing package {package_name}_v{version} ...")
        if isinstance(folder, str):
            folder = Folder(folder)
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        if folder == Folder.SavedGames:
            reference = server
            os.makedirs(os.path.join(path, '.' + reference.instance.name), exist_ok=True)
        else:
            reference = server.node
            os.makedirs(os.path.join(path, '.' + reference.name), exist_ok=True)
        try:
            filename = str(next(Path(path).glob(f"{package_name}*{version}*")))
        except StopIteration:
            if repo:
                await self.download_from_repo(repo, folder, package_name=package_name, version=version)
                return await self.install_package(reference, folder, package_name, version)
            return False
        try:
            return await self.do_install(reference, folder, package_name, version, path, filename)
        except Exception as ex:
            self.log.exception(ex)
            raise

    async def do_uninstall(self, reference: Union[Server, Node], folder: Folder, package_name: str, version: str,
                           packages_path: str) -> bool:
        target = reference.installation if folder == Folder.RootFolder else reference.instance.home
        async with aiofiles.open(os.path.join(packages_path, 'install.log'), mode='r', encoding=ENCODING) as log:
            lines = await log.readlines()
            for i in range(len(lines) - 1, 0, -1):
                filename = lines[i][2:].strip()
                file = os.path.normpath(os.path.join(target, filename))
                if lines[i].startswith('w'):
                    if os.path.isfile(file):
                        os.remove(file)
                    elif os.path.isdir(file) and not os.listdir(file):
                        with suppress(Exception):
                            os.removedirs(file)
                elif lines[i].startswith('x'):
                    try:
                        shutil.copy2(os.path.join(packages_path, filename), file)
                    except FileNotFoundError:
                        if folder == Folder.RootFolder:
                            self.log.warning(f"- Can't recover file {filename}, because it has been removed! "
                                             f"You might need to run a slow repair.")
        utils.safe_rmtree(packages_path)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    DELETE FROM mm_packages 
                    WHERE server_name = %s AND folder = %s AND package_name = %s AND version = %s
                """, (reference.name, folder, package_name, version))
        self.log.info(f"- Package {package_name}_v{version} successfully removed.")
        return True

    @proxy
    async def uninstall_package(self, server: Server, folder: Union[Folder, str], package_name: str,
                                version: str) -> bool:
        if isinstance(folder, str):
            folder = Folder(folder)
        if folder == Folder.RootFolder:
            return await self.uninstall_root_package(server.node, package_name, version)
        self.log.info(f"Uninstalling package {package_name}_v{version} ...")
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        packages_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        if not os.path.exists(os.path.join(packages_path, 'install.log')):
            self.log.warning(f"- Can't find {os.path.join(packages_path, 'install.log')}. Trying to recreate ...")
            # try to recreate it
            if not await self.recreate_install_log(server, folder, package_name, version):
                self.log.error(f"- Recreation failed. Can't uninstall {package_name}.")
                return False
            else:
                self.log.info("- Recreation successful.")
        return await self.do_uninstall(server, folder, package_name, version, packages_path)

    async def uninstall_root_package(self, node: Node, package_name: str, version: str) -> bool:
        self.log.info(f"Uninstalling root-package {package_name}_v{version} ...")
        folder = Folder.RootFolder
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        packages_path = os.path.join(path, '.' + node.name, package_name + '_v' + version)
        if not os.path.exists(os.path.join(packages_path, 'install.log')):
            self.log.warning(f"- Can't find {os.path.join(packages_path, 'install.log')}. Trying to recreate ...")
            # try to recreate it
            if not await self.recreate_install_log(node, folder, package_name, version):
                self.log.error(f"- Recreation failed. Can't uninstall {package_name}.")
                return False
            else:
                self.log.info("- Recreation successful.")
        return await self.do_uninstall(node, folder, package_name, version, packages_path)
