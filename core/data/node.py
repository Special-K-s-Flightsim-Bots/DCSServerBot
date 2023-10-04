from enum import Enum, auto
from pathlib import Path
from typing import Union, Optional, Tuple

from .instance import Instance
from .server import Server

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

__all__ = [
    "Node",
    "UploadStatus"
]


class UploadStatus(Enum):
    OK = auto()
    FILE_EXISTS = auto()
    FILE_IN_USE = auto()
    READ_ERROR = auto()
    WRITE_ERROR = auto()


class Node:

    def __init__(self, name: str):
        self.name = name
        self.instances: list[Instance] = list()
        self.locals = None
        self.config = self.read_config()

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
        raise NotImplemented()

    @staticmethod
    def read_config():
        config = yaml.load(Path('config/main.yaml').read_text(encoding='utf-8'))
        # set defaults
        config['logging'] = config.get('logging', {})
        config['logging']['loglevel'] = config['logging'].get('loglevel', 'DEBUG')
        config['logging']['logrotate_size'] = config['logging'].get('logrotate_size', 10485760)
        config['logging']['logrotate_count'] = config['logging'].get('logrotate_count', 5)
        config['database']['pool_min'] = config['database'].get('pool_min', 5)
        config['database']['pool_max'] = config['database'].get('pool_max', 10)
        config['messages'] = config.get('messages', {})
        config['messages']['player_username'] = config['messages'].get('player_username',
                                                                       'Your player name contains invalid characters. '
                                                                       'Please change your name to join our server.')
        config['messages']['player_default_username'] = \
            config['messages'].get('player_default_username', 'Please change your default player name at the top right '
                                                              'of the multiplayer selection list to an individual one!')
        config['messages']['player_banned'] = config['messages'].get('player_banned', 'You are banned from this '
                                                                                      'server. Reason: {}')
        return config

    def read_locals(self) -> dict:
        raise NotImplemented()

    async def upgrade(self) -> None:
        raise NotImplemented()

    async def update(self, warn_times: list[int]):
        raise NotImplemented()

    async def get_dcs_branch_and_version(self) -> Tuple[str, str]:
        raise NotImplemented()

    async def handle_module(self, what: str, module: str) -> None:
        raise NotImplemented()

    async def get_installed_modules(self) -> list[str]:
        raise NotImplemented()

    async def get_available_modules(self, userid: Optional[str] = None, password: Optional[str] = None) -> list[str]:
        raise NotImplemented()

    async def read_file(self, path: str) -> Union[bytes, int]:
        raise NotImplemented()

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        raise NotImplemented()

    async def list_directory(self, path: str, pattern: str) -> list[str]:
        raise NotImplemented()

    async def rename_server(self, server: Server, new_name: str, update_settings: Optional[bool] = False):
        raise NotImplemented()

    async def add_instance(self, name: str, *, template: Optional[Instance] = None) -> Instance:
        raise NotImplemented()

    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        raise NotImplemented()

    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        raise NotImplemented()

    async def find_all_instances(self) -> list[Tuple[str, str]]:
        raise NotImplemented()

    async def migrate_server(self, server: Server, instance: Instance) -> None:
        raise NotImplemented()

    async def unregister_server(self, server: Server) -> None:
        raise NotImplemented()
