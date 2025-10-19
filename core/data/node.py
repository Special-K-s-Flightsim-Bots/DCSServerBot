from __future__ import annotations

import aiohttp
import logging
import os

from core import utils
from core.translations import get_translation
from core.utils.helper import YAMLError
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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


class Node:

    def __init__(self, name: str, config_dir: str | None = 'config'):
        self.name = name
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.config_dir = config_dir
        self.instances: list[Instance] = list()
        self.locals = None
        self.config = self.read_config(os.path.join(config_dir, 'main.yaml'))
        self.guild_id: int = int(self.config['guild_id'])
        self.dcs_version = None
        self.slow_system: bool = False
        self.is_remote: bool = False

    def __repr__(self):
        return self.name

    @property
    def master(self) -> bool:
        raise NotImplementedError()

    @master.setter
    def master(self, value: bool):
        raise NotImplementedError()

    @property
    def public_ip(self) -> str:
        raise NotImplementedError()

    @property
    def installation(self) -> str:
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
                    utils.set_password('clusterdb', url.password, self.config_dir)
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
            raise FatalException()
        except MarkedYAMLError as ex:
            raise YAMLError(file, ex)

    def read_locals(self) -> dict:
        raise NotImplementedError()

    async def shutdown(self, rc: int = -2):
        raise NotImplementedError()

    async def restart(self):
        raise NotImplementedError()

    async def upgrade_pending(self) -> bool:
        raise NotImplementedError()

    async def upgrade(self):
        raise NotImplementedError()

    async def dcs_update(self, branch: str | None = None, version: str | None = None,
                         warn_times: list[int] = None, announce: bool | None = True):
        raise NotImplementedError()

    async def dcs_repair(self, warn_times: list[int] = None, slow: bool | None = False,
                         check_extra_files: bool | None = False):
        raise NotImplementedError()

    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        raise NotImplementedError()

    async def handle_module(self, what: str, module: str) -> None:
        raise NotImplementedError()

    async def get_installed_modules(self) -> list[str]:
        raise NotImplementedError()

    async def get_available_modules(self) -> list[str]:
        raise NotImplementedError()

    async def get_available_dcs_versions(self, branch: str) -> list[str] | None:
        raise NotImplementedError()

    async def get_latest_version(self, branch: str) -> str | None:
        raise NotImplementedError()

    async def shell_command(self, cmd: str, timeout: int = 60) -> tuple[str, str] | None:
        raise NotImplementedError()

    async def read_file(self, path: str) -> bytes | int:
        raise NotImplementedError()

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        raise NotImplementedError()

    async def list_directory(self, path: str, *, pattern: str | list[str] = '*',
                             order: SortOrder = SortOrder.DATE,
                             is_dir: bool = False, ignore: list[str] = None, traverse: bool = False
                             ) -> tuple[str, list[str]]:
        raise NotImplementedError()

    async def create_directory(self, path: str):
        raise NotImplementedError()

    async def remove_file(self, path: str):
        raise NotImplementedError()

    async def rename_file(self, old_name: str, new_name: str, *, force: bool | None = False):
        raise NotImplementedError()

    async def rename_server(self, server: Server, new_name: str):
        raise NotImplementedError()

    async def add_instance(self, name: str, *, template: str = "") -> Instance:
        raise NotImplementedError()

    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        raise NotImplementedError()

    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        raise NotImplementedError()

    async def find_all_instances(self) -> list[tuple[str, str]]:
        raise NotImplementedError()

    async def migrate_server(self, server: Server, instance: Instance) -> None:
        raise NotImplementedError()

    async def unregister_server(self, server: Server) -> None:
        raise NotImplementedError()

    async def install_plugin(self, plugin: str) -> bool:
        raise NotImplementedError()

    async def uninstall_plugin(self, plugin: str) -> bool:
        raise NotImplementedError()

    async def get_cpu_info(self) -> bytes | int:
        raise NotImplementedError()
