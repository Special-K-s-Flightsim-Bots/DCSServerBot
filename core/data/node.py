from __future__ import annotations

import aiohttp
import logging
import os

from abc import ABC, abstractmethod
from core import utils
from core.translations import get_translation
from core.utils.helper import YAMLError
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, unquote

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import Server, Instance

__all__ = [
    "Node",
    "UploadStatus",
    "SortOrder",
    "InstallationException",
    "FatalException"
]


_ = get_translation('core')


class UploadStatus(Enum):
    OK = auto()
    FILE_EXISTS = auto()
    FILE_IN_USE = auto()
    READ_ERROR = auto()
    WRITE_ERROR = auto()


class SortOrder(Enum):
    NAME = auto()
    DATE = auto()


class FatalException(Exception):
    def __init__(self, message: str | None = None):
        super().__init__(message)


class InstallationException(FatalException):
    def __init__(self, message: str | None = None):
        super().__init__(message)


class Node(ABC):

    def __init__(self, name: str, config_dir: str = 'config', restarted: bool = False):
        self.name = name
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.config_dir : str = config_dir
        self.instances: dict[str, Instance] = {}
        self.locals = None
        self.config = self.read_config(os.path.join(config_dir, 'main.yaml'))
        # (temporarily) disable validation on restarts (due to updates)
        if restarted:
            self.config['validation'] = 'none'
        self.guild_id: int = int(self.config['guild_id'])
        self.dcs_version = None
        self.slow_system: bool = False
        self.is_remote: bool = False

    def __repr__(self):
        return self.name

    @property
    @abstractmethod
    def master(self) -> bool:
        raise NotImplementedError()

    @master.setter
    @abstractmethod
    def master(self, value: bool):
        raise NotImplementedError()

    @property
    @abstractmethod
    def claimed_master(self) -> bool:
        raise NotImplementedError()

    @property
    @abstractmethod
    def public_ip(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def installation(self) -> str | None:
        raise NotImplementedError()

    @property
    def proxy(self) -> str | None:
        if 'proxy' not in self.locals:
            config = yaml.load(Path(os.path.join(self.config_dir, 'services', 'bot.yaml')).read_text(encoding='utf-8'))
            self.locals['proxy'] = config.get('proxy', {}).get('url')
        return self.locals['proxy']

    @property
    def proxy_auth(self) -> aiohttp.BasicAuth | None:
        if 'proxy_auth' not in self.locals:
            config = yaml.load(Path(os.path.join(self.config_dir, 'services', 'bot.yaml')).read_text(encoding='utf-8'))
            username = config.get('proxy', {}).get('username')
            try:
                password = utils.get_password('proxy', self.config_dir)
                self.locals['proxy_auth'] = aiohttp.BasicAuth(username, password)
            except ValueError:
                self.locals['proxy_auth'] = None
        return self.locals['proxy_auth']

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions', {})

    def read_config(self, file: str) -> dict:
        try:
            # we need to read first, otherwise we would not know the validation settings
            config = yaml.load(Path(file).read_text(encoding='utf-8'))
            validation = config.get('validation', 'lazy')
            if validation in ['strict', 'lazy']:
                utils.validate(file, ['schemas/main_schema.yaml'], raise_exception=(validation == 'strict'))

            # check if we need to secure the database URL
            database_url = config.get('database', {}).get('url')
            if database_url:
                url = urlparse(database_url)
                if url.password != 'SECRET':
                    utils.set_password('clusterdb', unquote(url.password), self.config_dir)
                    port = url.port or 5432
                    config['database']['url'] = \
                        f"{url.scheme}://{url.username}:SECRET@{url.hostname}:{port}{url.path}?sslmode=prefer"
                    with open(file, 'w', encoding='utf-8') as f:
                        yaml.dump(config, f)
                    self.log.info("Database password found, removing it from config.")

            # set defaults
            config['autoupdate'] = config.get('autoupdate', False)
            config['logging'] = config.get('logging', {})
            config['logging']['loglevel'] = config['logging'].get('loglevel', 'DEBUG')
            config['logging']['logrotate_size'] = config['logging'].get('logrotate_size', 10485760)
            config['logging']['logrotate_count'] = config['logging'].get('logrotate_count', 5)
            config['logging']['utc'] = config['logging'].get('utc', True)
            config['chat_command_prefix'] = config.get('chat_command_prefix', '-')
            return config
        except FileNotFoundError:
            raise InstallationException("No main.yaml found.")
        except MarkedYAMLError as ex:
            raise YAMLError(file, ex)

    @abstractmethod
    def read_locals(self) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def shutdown(self, rc: int = -2):
        raise NotImplementedError()

    @abstractmethod
    async def restart(self):
        raise NotImplementedError()

    @abstractmethod
    async def upgrade_pending(self) -> bool:
        raise NotImplementedError()

    @abstractmethod
    async def upgrade(self):
        raise NotImplementedError()

    @abstractmethod
    async def dcs_update(self, branch: str | None = None, version: str | None = None,
                         warn_times: list[int] = None, announce: bool | None = True):
        raise NotImplementedError()

    @abstractmethod
    async def dcs_repair(self, warn_times: list[int] = None, slow: bool | None = False,
                         check_extra_files: bool | None = False):
        raise NotImplementedError()

    @abstractmethod
    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        raise NotImplementedError()

    @abstractmethod
    async def handle_module(self, what: str, module: str) -> int:
        raise NotImplementedError()

    @abstractmethod
    async def get_installed_modules(self) -> list[str]:
        raise NotImplementedError()

    @abstractmethod
    async def get_available_modules(self) -> list[str]:
        raise NotImplementedError()

    @abstractmethod
    async def get_available_dcs_versions(self, branch: str) -> list[str] | None:
        raise NotImplementedError()

    @abstractmethod
    async def get_latest_version(self, branch: str) -> str | None:
        raise NotImplementedError()

    @abstractmethod
    async def shell_command(self, cmd: str, timeout: int = 60) -> tuple[str, str] | None:
        raise NotImplementedError()

    @abstractmethod
    async def read_file(self, path: str) -> bytes | int:
        raise NotImplementedError()

    @abstractmethod
    async def read_file_window(self, path: str, *, length: int, offset: int | None = None,
                               behind: int | None = None) -> tuple[bytes, int, int | float] | tuple[int, int, int | float]:
        """A WINDOW of a file — never the whole file — plus its size and IDENTITY at read time (L1).

        A read-only, WINDOWED sibling of :meth:`read_file`, for files too large to move whole (a
        running server's ``dcs.log``). Exactly one of ``offset`` / ``behind`` names the window:

        * ``offset`` — the bytes AT ``offset``, up to ``length`` of them; the way a FOLLOWER
          continues from where it last stopped;
        * ``behind`` — the bytes ENDING just before ``behind``, up to ``length`` of them; the way a
          reader PAGES BACK through a file it cannot move whole.

        It returns ``(window, size, identity)``:

        * ``size`` — the file's size AT READ TIME: the reader needs it to notice a truncated/rotated
          file (``size < offset`` means a NEW log) and to page correctly;
        * ``identity`` — a value that does NOT change when the file is APPENDED to but DOES change
          when it is REPLACED (``st_ctime`` where that is a creation time, e.g. Windows; the inode on
          POSIX, where ``st_ctime`` is an inode-change time). It is what lets a follower see a
          rotation even when the replacement file is ALREADY BIGGER than the offset it holds.

        An ``offset`` at or past the end yields an EMPTY window; a window longer than the file yields
        the file; ``length`` is CLAMPED to the primitive's own ceiling (``NODE_READ_WINDOW_MAX_BYTES``
        in ``nodeimpl``), so the window can never become a whole-file transfer. A missing file raises
        ``FileNotFoundError`` and an unreadable one ``PermissionError`` — the same failures the
        whole-file read already has, and no new ones.

        The whole-file :meth:`read_file`, its callers and the mission transfer path are UNTOUCHED: a
        caller that does not ask for a window gets exactly today's behaviour.
        """
        raise NotImplementedError()

    @abstractmethod
    async def write_file(self, target: str, source: str | int, overwrite: bool = False) -> UploadStatus:
        raise NotImplementedError()

    @abstractmethod
    async def list_directory(self, path: str, *, pattern: str | list[str] = '*',
                             order: SortOrder = SortOrder.DATE,
                             is_dir: bool = False, ignore: list[str] = None, traverse: bool = False
                             ) -> tuple[str, list[str]]:
        raise NotImplementedError()

    @abstractmethod
    async def list_files(self, path: str, *, pattern: str | list[str] = '*'
                         ) -> list[tuple[str, int, float]]:
        """The FILES matching *pattern* in *path*, each as ``(path, size, mtime)``, NEWEST FIRST (L2).

        A read-only sibling of :meth:`list_directory`, adding the two facts a download list needs and
        ``list_directory`` does not carry: each file's SIZE in bytes and its MODIFICATION TIME (a Unix
        epoch second). The result is ordered by ``mtime`` DESCENDING — the newest file first — so an
        operator choosing between several logs sees which one is which without a second call.

        The paths are ABSOLUTE, resolved on the node that answers (exactly as :meth:`list_directory`
        does), so a caller never builds a path from request data: it enumerates, and selects an entry
        by the identity the enumeration itself produced. A directory that does not exist yields the
        empty list (there are simply no files), never an error.
        """
        raise NotImplementedError()

    @abstractmethod
    async def create_directory(self, path: str):
        raise NotImplementedError()

    @abstractmethod
    async def remove_file(self, path: str):
        raise NotImplementedError()

    @abstractmethod
    async def rename_file(self, old_name: str, new_name: str, *, force: bool | None = False):
        raise NotImplementedError()

    @abstractmethod
    async def rename_server(self, server: Server, new_name: str):
        raise NotImplementedError()

    @abstractmethod
    async def add_instance(self, name: str, *, template: str = "") -> Instance:
        raise NotImplementedError()

    @abstractmethod
    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def find_all_instances(self) -> dict[str, str]:
        raise NotImplementedError()

    @abstractmethod
    async def migrate_server(self, server: Server, instance: Instance) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def unregister_server(self, server: Server) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def install_plugin(self, plugin: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    async def uninstall_plugin(self, plugin: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    async def get_cpu_info(self, used: bool = True, export: bool = False) -> bytes | dict | int:
        raise NotImplementedError()

    @abstractmethod
    async def info(self) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def get_config(self) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def set_config(self, config: dict) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def is_alive(self, timeout: int = 30) -> bool:
        raise NotImplementedError()
