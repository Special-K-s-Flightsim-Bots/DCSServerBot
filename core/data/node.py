import yaml
from pathlib import Path

from core.data.instance import Instance


class Node:

    def __init__(self, name: str):
        self.name = name
        self.instances: list[Instance] = list()
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
        config = yaml.safe_load(Path('config/main.yaml').read_text())
        # set defaults
        config['autoupdate'] = config.get('autoupdate', True)
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
        config['messages']['player_afk'] = config['messages'].get('player_afk',
                                                                  '{player.name}, you have been kicked for being AFK '
                                                                  'for more than {time}.')
        return config
