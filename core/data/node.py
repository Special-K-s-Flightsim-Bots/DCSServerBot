import logging
import os

from core import utils
from core.translations import get_translation
from enum import Enum, auto
from pathlib import Path
from typing import Union, Optional, TYPE_CHECKING
from urllib.parse import urlparse

from ..utils.helper import YAMLError

# ruamel YAML support
from pykwalify.errors import SchemaError, CoreError
from pykwalify.core import Core
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
    def __init__(self, message: Optional[str] = None):
        super().__init__(message)


class Node:

    def __init__(self, name: str, config_dir: Optional[str] = 'config'):
        self.name = name
        self.log = logging.getLogger(__name__)
        self.config_dir = config_dir
        self.instances: list["Instance"] = list()
        self.locals = None
        self.config = self.read_config(os.path.join(config_dir, 'main.yaml'))
        self.guild_id: int = int(self.config['guild_id'])
        self.dcs_version = None
        self.slow_system: bool = False

    def __repr__(self):
        return self.name

    @property
    def master(self) -> bool:
        raise NotImplemented()

    @master.setter
    def master(self, value: bool):
        raise NotImplemented()

    @property
    def public_ip(self) -> str:
        raise NotImplemented()

    @property
    def installation(self) -> str:
        raise NotImplemented()

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions', {})

    def read_config(self, file: str) -> dict:
        try:
            c = Core(source_file=file, schema_files=['schemas/main_schema.yaml'], file_encoding='utf-8')
            try:
                c.validate(raise_exception=True)
            except SchemaError as ex:
                self.log.warning(f'Error while parsing {file}:\n{ex}')
            config = yaml.load(Path(file).read_text(encoding='utf-8'))
            # check if we need to secure the database URL
            database_url = config.get('database', {}).get('url')
            if database_url:
                url = urlparse(database_url)
                if url.password != 'SECRET':
                    utils.set_password('database', url.password, self.config_dir)
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
            config['chat_command_prefix'] = config.get('chat_command_prefix', '-')
            return config
        except (FileNotFoundError, CoreError):
            raise FatalException()
        except MarkedYAMLError as ex:
            raise YAMLError(file, ex)

    def read_locals(self) -> dict:
        raise NotImplemented()

    async def shutdown(self, rc: int = -2):
        raise NotImplemented()

    async def restart(self):
        raise NotImplemented()

    async def upgrade_pending(self) -> bool:
        raise NotImplemented()

    async def upgrade(self):
        raise NotImplemented()

    async def update(self, warn_times: list[int], branch: Optional[str] = None, version: Optional[str] = None) -> int:
        raise NotImplemented()

    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        raise NotImplemented()

    async def handle_module(self, what: str, module: str) -> None:
        raise NotImplemented()

    async def get_installed_modules(self) -> list[str]:
        raise NotImplemented()

    async def get_available_modules(self) -> list[str]:
        raise NotImplemented()

    async def get_available_dcs_versions(self, branch: str) -> Optional[list[str]]:
        raise NotImplemented()

    async def get_latest_version(self, branch: str) -> Optional[str]:
        raise NotImplemented()

    async def shell_command(self, cmd: str, timeout: int = 60) -> Optional[tuple[str, str]]:
        raise NotImplemented()

    async def read_file(self, path: str) -> Union[bytes, int]:
        raise NotImplemented()

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        raise NotImplemented()

    async def list_directory(self, path: str, *, pattern: Union[str, list[str]] = '*',
                             order: SortOrder = SortOrder.DATE,
                             is_dir: bool = False, ignore: list[str] = None, traverse: bool = False
                             ) -> tuple[str, list[str]]:
        raise NotImplemented()

    async def create_directory(self, path: str):
        raise NotImplemented()

    async def remove_file(self, path: str):
        raise NotImplemented()

    async def rename_file(self, old_name: str, new_name: str, *, force: Optional[bool] = False):
        raise NotImplemented()

    async def rename_server(self, server: "Server", new_name: str):
        raise NotImplemented()

    async def add_instance(self, name: str, *, template: str = "") -> "Instance":
        raise NotImplemented()

    async def delete_instance(self, instance: "Instance", remove_files: bool) -> None:
        raise NotImplemented()

    async def rename_instance(self, instance: "Instance", new_name: str) -> None:
        raise NotImplemented()

    async def find_all_instances(self) -> list[tuple[str, str]]:
        raise NotImplemented()

    async def migrate_server(self, server: "Server", instance: "Instance") -> None:
        raise NotImplemented()

    async def unregister_server(self, server: "Server") -> None:
        raise NotImplemented()
