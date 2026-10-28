import asyncio
import glob
import os
import re
import shutil
import zipfile

from aiohttp import ClientSession, ClientResponseError
from contextlib import suppress
from core import ServiceRegistry, Service, Server, Status, ServiceInstallationError, utils, proxy, Node, Instance
from enum import Enum
from filecmp import cmp
from functools import total_ordering
from packaging import version
from pathlib import Path, PurePosixPath
from psycopg import sql
from typing import cast
from urllib.parse import urlparse

from ..servicebus import ServiceBus

__all__ = [
    "Folder",
    "ModManagerService"
]

#import sys
#if sys.platform == 'win32':
#    ENCODING = 'cp1252'
#else:
ENCODING = 'utf-8'


def _leaf_name(value: object) -> str | None:
    """The one leaf name *value* is, or ``None`` when it names a LOCATION instead.

    A package name and a version are interpolated into a glob and joined onto a package-folder path, so
    both are read through this: the answer is the value the join has to use. Surrounding whitespace is
    dropped here, so the string that was validated is the string that reaches the filesystem.

    Refused: an empty value, a NUL, a path separator, ``.``/``..`` and every absolute form. Everything
    else is accepted — the enumerator decides what a package is called.
    """
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return None
    if "/" in text or "\\" in text:
        return None
    if text in (".", ".."):
        return None
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        return None
    return text


class _EscapingArchiveEntry(Exception):
    """An archive entry that names a place outside the tree it would be written to."""

    def __init__(self, entry: str):
        super().__init__(entry)
        self.entry = entry


def _inside(base: object, candidate: object) -> bool:
    """True when *candidate* names an entry strictly BELOW *base*, never *base* itself and never outside.

    An archive entry name and an ``install.log`` line are content, not a caller's identity: both are
    joined onto a target, so both are read through this before the join is built. A separator is a
    separator in either direction — Windows spells it ``\\`` where POSIX reads an ordinary character —
    so both sides are folded to ``/`` first. A rooted path, a drive-qualified path and a NUL are outside
    by definition; a different drive on Windows has no common path and is outside too.
    """
    if not base or candidate is None:
        return False
    base_text = str(base).strip()
    text = str(candidate).strip()
    if not base_text or not text or "\x00" in text:
        return False
    folded = text.replace("\\", "/")
    if folded.startswith("/"):
        return False
    if len(folded) > 1 and folded[1] == ":" and folded[0].isalpha():
        return False
    base_norm = os.path.normpath(base_text).replace("\\", "/").rstrip("/") or "/"
    joined = os.path.normpath(base_norm + "/" + folded).replace("\\", "/")
    try:
        common = os.path.commonpath([base_norm, joined])
    except ValueError:
        return False
    if os.path.normcase(common) != os.path.normcase(base_norm):
        return False
    return os.path.normcase(joined) != os.path.normcase(base_norm)


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
        if not self.locals:
            raise ServiceInstallationError(service=self.name, reason="config/services/modmanager.yaml missing!")
        self.bus = None
        self.temp_packages = []

    async def start(self):
        await super().start()
        self.bus = ServiceRegistry.get(ServiceBus)
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

            try:
                for package in config.get('packages', []):
                    folder = Folder(package['source'])
                    if package.get('version', 'latest') == 'latest':
                        _version = await self.get_latest_version(package)
                    else:
                        _version = package.get('version')
                        if _version is not None:
                            _version = str(_version)
                    # check for valid versions
                    if _version is None:
                        self.log.warning(f"{package['name']} without version in {folder.name}, skipped!")
                        continue

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
                            reference = server.node if folder == Folder.RootFolder else server
                            blocking = self._blocking_servers(reference, folder)
                            if blocking:
                                self.log.warning(
                                    f"  - Server {server.name} cannot auto-install package {package['name']}: "
                                    f"{folder.value} is held by server(s) {', '.join(blocking)}.")
                                break
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
                    except Exception as ex:
                        self.log.error(f"Error while installing package {package['name']}_v{_version}: {ex}")
                    finally:
                        if not maintenance:
                            server.maintenance = False
            except Exception as ex:
                self.log.error(f"Error while installing packages for server {server.name}: {ex}")

    @staticmethod
    def parse_filename(filename: str) -> tuple[str | None, str | None]:
        if filename.endswith('.zip'):
            filename = filename[:-4]
        exp = re.compile(r'(?P<package>.*?)(?:v)?(?P<version>[0-9]+(?:\.[A-Za-z0-9._-]+)?)$')
        match = exp.match(filename)
        if match:
            return (match.group('package').rstrip('v').rstrip('_').rstrip('-').rstrip('.').strip(),
                    match.group('version').strip())
        else:
            return None, None

    async def get_installed_packages(self, reference: Server | Node, folder: Folder) -> list[tuple[str, str]]:
        if isinstance(reference, Server):
            column = 'server_name'
        else:
            column = 'node'
        async with self.apool.connection() as conn:
            cursor = await conn.execute(f"""
                SELECT package_name, version FROM mm_packages 
                WHERE {column} = %s AND folder = %s 
                ORDER BY package_name, version
            """, (reference.name, folder.value))
            return [
                (x[0], x[1]) async for x in cursor
            ]

    async def get_repo_versions(self, repo: str) -> set[str]:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
        return set([x['tag_name'].strip('v') for x in data])

    async def _release_tags(self, repo: str) -> list[str]:
        """The release tags of ``repo`` in the order GitHub lists them, deduplicated, ``v`` stripped.

        The same API call and the same tag vocabulary as :meth:`get_repo_versions`, read as an
        ORDERED list: GitHub answers a repository's releases newest first, and a version pick that
        re-sorted them would put ``2.4.1`` above ``2.10.0``.
        """
        api = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        async with ClientSession() as session:
            async with session.get(api, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
        tags: list[str] = []
        for release in data or ():
            tag = str(release.get('tag_name', '')).strip('v')
            if tag and tag not in tags:
                tags.append(tag)
        return tags

    @proxy(timeout=60)
    async def list_repo_versions(self, node: Node, url: str) -> list[str]:
        """The releases of the repository ``url``, newest first — ``[]`` for any other URL.

        Read on ``node``: the GitHub request goes out through that node's proxy, exactly like every
        other download this service performs, so the answer comes from the network the packages will
        be fetched over. A URL that is not a repository is answered without a request at all.
        """
        if not self.is_repo_url(url):
            return []
        return await self._release_tags(url)

    async def get_download_url(self, repo: str, package_name: str, version: str) -> str | list[str] | None:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
        element = next((x for x in data if x['tag_name'].strip('v') == version), None)
        if not element:
            return None
        for asset in element.get('assets', []):
            if version in asset['name'] or '.'.join(version.split('.')[:-1]) in asset['name']:
                _name, _ = self.parse_filename(asset['name'])
                if _name == package_name:
                    return asset['browser_download_url']
            elif asset['name'] == f"{package_name}.zip":
                return asset['browser_download_url']
        else:
            try:
                assets = [asset['browser_download_url'] for asset in element.get('assets', [])]
                return assets[0] if len(assets) == 1 else assets
            except (KeyError, IndexError):
                return None

    def _package_dir(self, folder: Folder) -> str:
        """The package directory of ``folder`` as configured for the node this service runs on."""
        return os.path.expandvars(self.get_config()[folder.value])

    def _package_identity(self, package_name: object, version: object) -> tuple[str, str] | None:
        """``(package, version)`` as the identity this call may join onto a path, or ``None``.

        The returned pair is the only form a caller-supplied name and version reach a glob, a path or a
        row through, so a value naming a location is refused here — with a warning line, never a raise —
        before any join is built.
        """
        package = _leaf_name(package_name)
        _version = _leaf_name(version)
        if package is None or _version is None:
            self.log.warning(
                f"- {package_name!r} v{version!r} is not a package identity, refused: a package name "
                f"and a version are one entry each, not a path.")
            return None
        return package, _version

    @proxy
    async def list_available_packages(self, node: Node, folder: Folder | str) -> list[dict]:
        """
        Enumerate the package directory of ``folder`` on ``node``.

        One entry per package: ``name``, ``version``, ``kind`` (``zip`` or ``dir``), ``size`` in bytes and
        ``mtime``. Both a zip file and a directory are packages. Hidden entries, ``desktop.ini``, entries of
        any other type and names that do not parse are skipped; a missing package directory yields an empty
        list.
        """
        if isinstance(folder, str):
            folder = Folder(folder)
        path = self._package_dir(folder)
        try:
            entries = sorted(os.listdir(path))
        except FileNotFoundError:
            self.log.warning(f"- ModManager: package folder {folder.value} does not exist.")
            return []
        packages = []
        for entry in entries:
            if entry.startswith('.') or entry.casefold() == 'desktop.ini':
                continue
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                kind = 'dir'
            elif entry.lower().endswith('.zip') and os.path.isfile(full):
                kind = 'zip'
            else:
                continue
            name, _version = self.parse_filename(entry)
            if not name or not _version:
                self.log.debug(f"- ModManager: {folder.value}/{entry} is not a package, skipped.")
                continue
            packages.append({
                'name': name,
                'version': _version,
                'kind': kind,
                'size': self._package_size(full) if kind == 'dir' else os.path.getsize(full),
                'mtime': os.path.getmtime(full)
            })
        return packages

    @staticmethod
    def _package_size(path: str) -> int:
        """Total size of the files below ``path``; a package directory carries no size of its own."""
        size = 0
        for root, _dirs, files in os.walk(path):
            for file in files:
                with suppress(OSError):
                    size += os.path.getsize(os.path.join(root, file))
        return size

    async def get_available_versions(self, reference: Server | Node, folder: Folder,
                                     package_name: str) -> list[str]:
        """Every version of ``package_name`` the folder and the package's repository offer.

        *reference* is the server the package belongs to, or the node itself when the caller owns no
        instance (a node's ``RootFolder``): a node reference reads the configuration block without an
        instance override, which is the block both folders are configured in.
        """
        leaf = _leaf_name(package_name)
        if leaf is None:
            return []
        local_versions: set[str] = set()
        config = self.get_config(reference if isinstance(reference, Server) else None)
        # the name is literal identity: ``*``, ``?``, ``[`` and ``]`` in it are characters, not a pattern
        for x in Path(os.path.expandvars(config[folder.value])).glob(f"{glob.escape(leaf)}*"):
            name, version = self.parse_filename(x.name)
            if version:
                local_versions.add(version)
        remote_versions: set[str] = set()
        with suppress(StopIteration):
            package = next(x for x in config.get('packages', []) if x['name'] == leaf and x['source'] == folder.value)
            if 'repo' in package:
                remote_versions = {x for x in await self.get_repo_versions(package['repo'])}
        return sorted(local_versions | remote_versions)

    @staticmethod
    def extract_repo_name(url: str) -> str:
        path = urlparse(url).path
        return path.lstrip('/')

    @staticmethod
    def is_repo_url(url: str) -> bool:
        """True for a GitHub repository URL (``owner/repo`` and nothing deeper)."""
        parsed = urlparse(url)
        if parsed.netloc.casefold() not in ('github.com', 'www.github.com'):
            return False
        return len([part for part in parsed.path.split('/') if part]) == 2

    async def download(self, url: str, folder: Folder, version: str | None, *, force: bool | None = False) -> None:
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        filename, _ = self.parse_filename(os.path.basename(url))
        if not filename:
            filename = os.path.basename(url)[:-4]
        if version and version not in filename:
            filename += f'_v{version}.zip'
        # the written name is ONE leaf of the package folder, whatever the URL or the version spells
        outpath = os.path.join(path, os.path.basename(filename))
        self.log.info(f"  => ModManager: Downloading {folder.value}/{filename} ...")
        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                if os.path.exists(outpath) and not force:
                    self.log.warning(f"  => ModManager: File {folder.value}/{filename} exists!")
                    raise FileExistsError(outpath)
                with open(outpath, mode='wb') as outfile:
                    outfile.write(await response.read())
        self.log.info(f"  => ModManager: {folder.value}/{filename} downloaded.")

    async def download_from_repo(self, repo: str, folder: Folder, *, package_name: str | None = None,
                                 version: str | None = None, force: bool | None = False) -> list[str] | None:
        if not package_name:
            package_name = self.extract_repo_name(repo).split('/')[-1]
        if not version or version == 'latest':
            version = await self.get_latest_repo_version(repo)
        url = await self.get_download_url(repo, package_name, version)
        # if the download list is ambiguous, return the whole list for a selection
        if isinstance(url, list):
            return url
        try:
            await self.download(url, folder, version, force=force)
        except ClientResponseError:
            url = f'{repo}/releases/download/v{version}/{package_name}_{version}.zip'
            await self.download(url, folder, version, force=force)

    @proxy(timeout=600)
    async def download_package(self, node: Node, folder: Folder | str, url: str, *, version: str | None = None,
                               package_name: str | None = None, force: bool = False) -> bool:
        """
        Download ``url`` into the package directory of ``folder`` on ``node``.

        A GitHub repository URL resolves one release asset; an answer offering several assets is refused with
        ``False``, because a version has to be named to pick one. Any other http(s) URL is fetched as given.
        A missing target folder, an existing file without ``force`` and a failing request raise as the
        download itself does.
        """
        if isinstance(folder, str):
            folder = Folder(folder)
        if package_name is not None:
            name = _leaf_name(package_name)
            if name is None:
                self.log.warning(f"- {package_name!r} is not a package name, nothing was downloaded.")
                return False
            package_name = name
        if version is not None:
            _version = _leaf_name(version)
            if _version is None:
                self.log.warning(f"- {version!r} is not a version, nothing was downloaded.")
                return False
            version = _version
        if self.is_repo_url(url):
            downloaded = await self.download_from_repo(url, folder, package_name=package_name,
                                                       version=version, force=force)
            return not isinstance(downloaded, list)
        await self.download(url, folder, version, force=force)
        return True

    async def get_latest_repo_version(self, repo: str) -> str:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases/latest"

        async with ClientSession() as session:
            async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get('tag_name', '').strip('v')

    async def _get_latest_file_version(self, package: dict):
        """The newest version of ``package``'s own files in its folder, ``v`` stripped, else ``None``.

        A folder holds every package side by side, so an entry counts for this package only when
        ``parse_filename`` reads this package's name out of it: a file whose name merely CONTAINS the
        name (``FooBar`` for ``Foo``) is the file of another package. An entry that does not parse is
        skipped rather than read as a version.
        """
        config = self.get_config()
        source = package['source']
        if isinstance(source, Folder):
            source = source.value
        path = os.path.expandvars(config[source])
        max_version = None
        for entry in os.listdir(path):
            name, _version = self.parse_filename(entry)
            if not _version or name != package['name']:
                continue
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

    @proxy(timeout=120)
    async def list_package_versions(self, node: Node, folder: Folder | str,
                                    package_names: list[str]) -> dict[str, dict]:
        """``{package: {"versions", "latest", "repo", "downloaded"}}`` for one package folder on ``node``.

        Computed by :meth:`get_available_versions` and :meth:`get_latest_version` themselves, so the
        answer is the chat client's own verdict — the folder's versions merged with a declared
        ``repo:``'s releases, and the newest of them (a repository's latest release, else the newest
        file on disk). ``repo`` is the declared repository URL (``""`` for a plain-URL package) and
        ``downloaded`` says whether ``latest`` is a file in the folder right now.

        ONE call per page for a whole table: the folder is enumerated once and read for every name.
        A package whose read raises is left out of the answer, so the caller falls back to what it
        knew before for that row alone rather than losing the table.
        """
        if isinstance(folder, str):
            folder = Folder(folder)
        declared = {package['name']: package
                    for package in (self.get_config().get('packages') or ())
                    if isinstance(package, dict) and package.get('name')
                    and package.get('source') == folder.value}
        on_disk: dict[str, set[str]] = {}
        for entry in await self.list_available_packages(node, folder):
            on_disk.setdefault(entry['name'], set()).add(entry['version'])
        answer: dict[str, dict] = {}
        for name in package_names or ():
            leaf = _leaf_name(name)
            if leaf is None:
                continue
            repo = str((declared.get(leaf) or {}).get('repo') or '')
            package = {"source": folder, "name": leaf}
            if repo:
                package['repo'] = repo
            try:
                versions = await self.get_available_versions(node, folder, leaf)
                latest = str(await self.get_latest_version(package) or '')
            except Exception as ex:
                self.log.warning(f"ModManager: the versions of {leaf} could not be read: {ex}")
                continue
            answer[leaf] = {
                "versions": [str(item) for item in versions],
                "latest": latest,
                "repo": repo,
                "downloaded": latest in on_disk.get(leaf, set()),
            }
        return answer

    async def get_installed_package(self, reference: Server | Node, folder: Folder, package_name: str) -> str | None:
        if isinstance(reference, Server):
            column = 'server_name'
        else:
            column = 'node'
        async with self.apool.connection() as conn:
            query = sql.SQL("""
                SELECT version FROM mm_packages 
                WHERE {column} = %s AND package_name = %s AND folder = %s
            """).format(column=sql.Identifier(column))
            cursor = await conn.execute(query, (reference.name, package_name, folder.value))
            return (await cursor.fetchone())[0] if cursor.rowcount == 1 else None

    async def recreate_install_log(
            self,
            reference: Server | Node,
            folder: Folder,
            package_name: str,
            version: str,
            force: bool = False,
            repo: str | None = None
    ) -> bool:
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        if folder == Folder.SavedGames:
            packages_path = os.path.join(path, '.' + reference.instance.name, package_name + '_v' + version)
            target = cast(Instance, cast(Server, reference).instance).home
        else:
            packages_path = os.path.join(path, '.' + reference.name, package_name + '_v' + version)
            target = cast(Node, reference).installation
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
            created_dirs = []
            with zipfile.ZipFile(package + '.zip', 'r') as zfile:
                for name in zfile.namelist():
                    if os.path.basename(name).lower() == 'thumbs.db':
                        continue
                    # an archive name may only be replayed against the trees it stays below
                    if not _inside(target, name) or not _inside(packages_path, name):
                        self.log.warning(
                            f"- install.log recreation for {package_name}_v{version} left out the "
                            f"archive entry {name!r}, it names a place outside {target}.")
                        continue
                    if name.endswith('/'):
                        name.rstrip('/')
                    else:
                        dirname = os.path.dirname(name)
                        if dirname not in created_dirs:
                            log_entries.append(f"w {os.path.dirname(name)}\n")
                            created_dirs.append(dirname)
                    log_entries.append(f"w {name}\n")

        package = await self._ensure_package(folder, package_name, version, repo=repo)
        if not package:
            if not force:
                self.log.error(f"- Recreation of install log for package {package_name}_v{version} failed, "
                               f"no source package available.")
                return False
        elif os.path.isdir(package):
            await asyncio.to_thread(recreate_normal_package)
        elif os.path.exists(package + '.zip'):
            await asyncio.to_thread(recreate_zip_package)

        with open(os.path.join(packages_path, 'install.log'), 'w', encoding=ENCODING) as log:
            log.writelines(log_entries)
        return True

    @staticmethod
    def is_ovgme(zfile: zipfile.ZipFile, package_name: str) -> bool:
        for zip_path in zfile.namelist():
            parts = zip_path.split('/')
            if (len(parts) >= 2 and package_name in parts[0] and
                    parts[1].lower() in ['coremods', 'mods', 'scripts', 'kneeboard', 'liveries']):
                return True
        return False

    async def do_install(self, reference: Server | Node, folder: Folder, package_name: str, version: str,
                         path: str, filename: str) -> bool:
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        if folder == Folder.SavedGames:
            target = reference.instance.home
            packages_path = os.path.join(path, '.' + reference.instance.name, package_name + '_v' + version)
        else:
            target = reference.installation
            packages_path = os.path.join(path, '.' + reference.name, package_name + '_v' + version)
        created_here = not os.path.isdir(packages_path)
        os.makedirs(packages_path, exist_ok=True)
        log_entries = []

        def process_zipfile():
            create_dirs = set()
            with zipfile.ZipFile(filename, 'r') as zfile:
                namelist = zfile.namelist()
                ovgme = self.is_ovgme(zfile, package_name)
                if ovgme:
                    root = PurePosixPath(namelist[0]).parts[0] + '/'
                entries = []
                for name in namelist:
                    if ovgme:
                        _name = name.replace(root, '')
                        if not _name or name in ['README.txt', 'VERSION.txt']:
                            continue
                    else:
                        _name = name
                    # ignore all Thumbs.db files
                    if os.path.basename(_name).lower() == 'thumbs.db':
                        continue
                    # an entry name is archive content: it may only name a place below both trees, and
                    # the whole archive is read before anything is written, so a refusal leaves the
                    # installation untouched instead of half-written
                    if not _inside(target, _name) or not _inside(packages_path, _name):
                        raise _EscapingArchiveEntry(name)
                    entries.append((name, _name))
                for name, _name in entries:
                    orig = os.path.join(target, _name)
                    parent = os.path.dirname(orig)

                    if os.path.exists(orig) and os.path.isfile(orig):
                        log_entries.append(f"x {_name}\n")
                        dest = os.path.join(packages_path, _name)
                        dest_parent = os.path.dirname(dest)
                        if dest_parent not in create_dirs:
                            os.makedirs(dest_parent, exist_ok=True)
                            create_dirs.add(dest_parent)
                        shutil.copyfile(orig, dest)
                    else:
                        if parent and not parent in create_dirs:
                            os.makedirs(parent, exist_ok=True)
                            create_dirs.add(parent)
                            log_entries.append(f"w {os.path.dirname(_name)}\n")
                        if _name.endswith('/'):
                            continue
                        log_entries.append(f"w {_name}\n")
                    with zfile.open(name) as infile:
                        with open(orig, mode='wb') as outfile:
                            shutil.copyfileobj(infile, outfile)
            return log_entries

        def copy_tree():
            skipped = []

            def backup(p, names) -> list[str]:
                ignore_list = []
                created_dirs = set()
                _dir = p[len(os.path.join(path, package_name + '_v' + version)):].lstrip(os.path.sep)
                for name in names:
                    # ignore all Thumbs.db files
                    if name.lower() == 'thumbs.db':
                        ignore_list.append(name)
                        continue
                    source = os.path.join(p, name)
                    if len(_dir):
                        name = os.path.join(_dir, name)
                    # a tree entry is walked from the package itself, but the log only carries names that
                    # stay below both trees; anything else is left out of the installation and the log
                    if not _inside(target, name) or not _inside(packages_path, name):
                        skipped.append(name)
                        ignore_list.append(os.path.basename(name))
                        continue
                    orig = os.path.join(target, name)
                    if os.path.exists(orig) and os.path.isfile(orig) and not cmp(source, orig):
                        log_entries.append("x {}\n".format(name.replace('\\', '/')))
                        dest = os.path.join(packages_path, name)
                        dest_parent = os.path.dirname(dest)
                        if dest_parent not in created_dirs:
                            os.makedirs(dest_parent, exist_ok=True)
                            created_dirs.add(dest_parent)
                        shutil.copy(orig, dest)
                    else:
                        log_entries.append("w {}\n".format(name.replace('\\', '/')))
                return ignore_list

            shutil.copytree(filename, target, ignore=backup, dirs_exist_ok=True)
            if skipped:
                self.log.warning(
                    f"- {len(skipped)} entr{'y' if len(skipped) == 1 else 'ies'} of "
                    f"{package_name}_v{version} name{'s' if len(skipped) == 1 else ''} a place outside "
                    f"{target}, left out.")

        if os.path.isfile(filename) and filename.endswith(".zip"):
            try:
                await asyncio.to_thread(process_zipfile)
            except _EscapingArchiveEntry as ex:
                self.log.warning(
                    f"- Installation of package {package_name}_v{version} refused: the archive entry "
                    f"{ex.entry!r} names a place outside {target}. Nothing was installed.")
                if created_here:
                    utils.safe_rmtree(packages_path)
                return False
        elif os.path.isdir(filename):
            await asyncio.to_thread(copy_tree)
        else:
            self.log.error(f"- Installation of package {package_name}_v{version} failed, no package.")
            return False
        with open(os.path.join(packages_path, 'install.log'), mode='w', encoding=ENCODING) as log:
            log.writelines(log_entries)

        if isinstance(reference, Server):
            column = 'server_name'
        else:
            column = 'node'

        async with self.apool.connection() as conn:
            await conn.execute(f"""
                INSERT INTO mm_packages ({column}, package_name, version, folder, time) 
                VALUES (%s, %s, %s, %s, timezone('UTC', now())) 
                ON CONFLICT ({column}, package_name) 
                DO UPDATE SET version=excluded.version
            """, (reference.name, package_name, version, folder.value))
        self.log.info(f"- Package {package_name}_v{version} successfully installed in {target}.")
        return True

    async def _ensure_package(self, folder: Folder, package_name: str, version: str,
                              repo: str | None = None) -> str | None:
        if isinstance(folder, str):
            folder = Folder(folder)
        identity = self._package_identity(package_name, version)
        if identity is None:
            return None
        package_name, version = identity
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        # the name and the version are literal identity: ``*``, ``?``, ``[`` and ``]`` in them are
        # characters, not a pattern, so the lookup cannot answer with a sibling package's archive
        pattern = f"{glob.escape(package_name)}*{glob.escape(version)}*"
        try:
            return str(next(Path(path).glob(pattern)))
        except StopIteration:
            if repo:
                try:
                    if await self.download_from_repo(repo, folder, package_name=package_name, version=version):
                        raise
                    return str(next(Path(path).glob(pattern)))
                except Exception:
                    pass
                self.log.warning(f"- Can't find {package_name}_v{version} in {repo}.")
            else:
                self.log.warning(f"- Can't find {package_name}_v{version} in {folder.value}.")
            return None

    async def _install_from_folder(self, reference: Server | Node, folder: Folder, package_name: str,
                                   version: str, path: str, repo: str | None = None) -> bool:
        """Install the package ``package_name``/``version`` from the folder ``path`` onto the target of
        ``reference``."""
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        filename = await self._ensure_package(folder, package_name, version, repo)
        if not filename:
            return False
        try:
            return await self.do_install(reference, folder, package_name, version, path, filename)
        except Exception as ex:
            self.log.exception(ex)
            raise

    def _registry_servers(self) -> list[Server]:
        """The servers registered in this process; empty when the service has no bus registry."""
        return list(getattr(getattr(self, 'bus', None), 'servers', {}).values())

    def _blocking_servers(self, reference: Server | Node, folder: Folder) -> list[str]:
        """Names of the servers whose state forbids a write to ``folder`` on ``reference``.

        ``RootFolder`` is the node's DCS installation: every server on that node must be down.
        ``SavedGames`` is one instance: only that server must be down. Servers of other nodes never count.
        """
        if folder == Folder.SavedGames:
            server = cast(Server, reference)
            return [] if server.status == Status.SHUTDOWN else [server.name]
        node_name = cast(Node, reference).name
        return sorted(
            server.name for server in self._registry_servers()
            if getattr(getattr(server, 'node', None), 'name', None) == node_name
            and server.status != Status.SHUTDOWN
        )

    def _gate_open(self, reference: Server | Node, folder: Folder) -> bool:
        """Whether a write to ``folder`` on ``reference`` may start; a closed gate names its blockers."""
        blocking = self._blocking_servers(reference, folder)
        if blocking:
            self.log.warning(
                f"- {folder.value} is held by server(s) {', '.join(blocking)}. "
                f"Shut them down first, nothing was written.")
            return False
        return True

    @proxy(timeout=180)
    async def install_package(self, server: Server, folder: Folder | str, package_name: str, version: str,
                              repo: str | None = None) -> bool:
        if isinstance(folder, str):
            folder = Folder(folder)
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        if folder == Folder.SavedGames:
            reference = server
            directory = '.' + cast(Instance, server.instance).name
        else:
            reference = server.node
            directory = '.' + server.node.name
        if not self._gate_open(reference, folder):
            return False
        self.log.info(f"Installing package {package_name}_v{version} ...")
        path = self._package_dir(folder)
        os.makedirs(os.path.join(path, directory), exist_ok=True)
        return await self._install_from_folder(reference, folder, package_name, version, path, repo)

    @proxy(timeout=180)
    async def install_root_package(self, node: Node, package_name: str, version: str,
                                   repo: str | None = None) -> bool:
        """Install a package into the DCS installation of ``node`` (``Folder.RootFolder``)."""
        folder = Folder.RootFolder
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        if not self._gate_open(node, folder):
            return False
        self.log.info(f"Installing root-package {package_name}_v{version} ...")
        path = self._package_dir(folder)
        os.makedirs(os.path.join(path, '.' + node.name), exist_ok=True)
        return await self._install_from_folder(node, folder, package_name, version, path, repo)

    async def do_uninstall(self, reference: Server | Node, folder: Folder, package_name: str, version: str,
                           packages_path: str) -> bool:
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        target = reference.installation if folder == Folder.RootFolder else reference.instance.home

        def _process_install_log() -> list[tuple[str, str]]:
            actions: list[tuple[str, str]] = []
            log_path = os.path.join(packages_path, 'install.log')
            with open(log_path, mode='r', encoding=ENCODING) as log:
                for line in log:
                    if len(line) < 3:
                        continue
                    action = line[0]
                    filename = line[2:].strip()
                    if filename:
                        actions.append((action, filename))
            return actions

        actions = await asyncio.to_thread(_process_install_log)

        def _apply_actions() -> int:
            skipped = 0
            for action, filename in reversed(actions):
                # a log line is content: a name resolving outside the target is never removed or
                # overwritten, and a backup is only read from below the package folder
                if not _inside(target, filename):
                    skipped += 1
                    self.log.warning(
                        f"- install.log entry {filename!r} names a place outside {target}, skipped.")
                    continue
                if action == 'x' and not _inside(packages_path, filename):
                    skipped += 1
                    self.log.warning(
                        f"- install.log entry {filename!r} names a place outside {packages_path}, skipped.")
                    continue
                file = os.path.normpath(os.path.join(target, filename))
                if action == 'w':
                    if os.path.isfile(file):
                        os.remove(file)
                    elif os.path.isdir(file):
                        with suppress(Exception):
                            # avoid loading the full directory listing when only emptiness matters
                            if next(os.scandir(file), None) is None:
                                os.removedirs(file)
                elif action == 'x':
                    try:
                        shutil.copy(os.path.join(packages_path, filename), file)
                    except FileNotFoundError:
                        if folder == Folder.RootFolder:
                            self.log.warning(
                                f"- Can't recover file {filename}, because it has been removed! "
                                f"You might need to run a slow repair."
                            )
            return skipped

        skipped = await asyncio.to_thread(_apply_actions)
        if skipped:
            self.log.warning(
                f"- {skipped} install.log entr{'y' if skipped == 1 else 'ies'} of "
                f"{package_name}_v{version} were skipped, they name a place outside the target.")
        utils.safe_rmtree(packages_path)

        if isinstance(reference, Server):
            column = 'server_name'
        else:
            column = 'node'
        async with self.apool.connection() as conn:
            await conn.execute(f"""
                DELETE FROM mm_packages 
                WHERE {column} = %s AND folder = %s AND package_name = %s AND version = %s
            """, (reference.name, folder.value, package_name, version))
        self.log.info(f"- Package {package_name}_v{version} successfully removed.")
        return True

    @proxy(timeout=180)
    async def uninstall_package(
            self,
            server: Server,
            folder: Folder | str,
            package_name: str,
            version: str,
            force: bool = False,
            repo: str | None = None
    ) -> bool:
        if isinstance(folder, str):
            folder = Folder(folder)
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        if folder == Folder.RootFolder:
            # the node's gate is read once, inside the method that runs on the node
            return await self.uninstall_root_package(server.node, package_name, version, force, repo)
        if not self._gate_open(server, folder):
            return False
        self.log.info(f"Uninstalling package {package_name}_v{version} ...")
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        packages_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        if not os.path.exists(os.path.join(packages_path, 'install.log')):
            self.log.warning(f"- Can't find {os.path.join(packages_path, 'install.log')}. Trying to recreate ...")
            # try to recreate it
            if not await self.recreate_install_log(server, folder, package_name, version, force, repo):
                self.log.error(f"- Recreation failed. Can't uninstall {package_name}.")
                return False
            else:
                self.log.info("- Recreation successful.")
        return await self.do_uninstall(server, folder, package_name, version, packages_path)

    @proxy(timeout=180)
    async def uninstall_root_package(
            self, node: Node,
            package_name: str,
            version: str,
            force: bool = False,
            repo: str | None = None
    ) -> bool:
        folder = Folder.RootFolder
        identity = self._package_identity(package_name, version)
        if identity is None:
            return False
        package_name, version = identity
        if not self._gate_open(node, folder):
            return False
        self.log.info(f"Uninstalling root-package {package_name}_v{version} ...")
        config = self.get_config()
        path = os.path.expandvars(config[folder.value])
        packages_path = os.path.join(path, '.' + node.name, package_name + '_v' + version)
        if not os.path.exists(os.path.join(packages_path, 'install.log')):
            self.log.warning(f"- Can't find {os.path.join(packages_path, 'install.log')}. Trying to recreate ...")
            # try to recreate it
            if not await self.recreate_install_log(node, folder, package_name, version, force, repo):
                self.log.error(f"- Recreation failed. Can't uninstall {package_name}.")
                return False
            else:
                self.log.info("- Recreation successful.")
        return await self.do_uninstall(node, folder, package_name, version, packages_path)
