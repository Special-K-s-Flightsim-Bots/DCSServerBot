import os

from copy import deepcopy
from core import DEFAULT_TAG, Plugin
from pathlib import Path

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


async def migrate_3_6(self: Plugin):
    filename = os.path.join(self.node.config_dir, 'plugins', 'userstats.yaml')
    if not os.path.exists(filename):
        return
    data = yaml.load(Path(filename).read_text(encoding='utf-8'))

    def migrate_instance(cfg: dict) -> dict:
        ret = {}
        for name, instance in cfg.items():
            if 'greeting_message_members' in instance:
                if name not in ret:
                    ret[name] = {}
                ret[name]['greeting_message_members'] = instance['greeting_message_members']
            if 'greeting_message_unmatched' in instance:
                if name not in ret:
                    ret[name] = {}
                ret[name]['greeting_message_unmatched'] = instance['greeting_message_unmatched']
        return ret

    dirty = False
    if self.node.name in data:
        for node_name, node in data.items():
            result = migrate_instance(node)
            if result:
                dirty = True
                if node_name not in self.locals:
                    self.locals[node_name] = result
                else:
                    self.locals[node_name] |= result
    else:
        result = migrate_instance(data)
        if result:
            dirty = True
            self.locals |= result
    if dirty:
        path = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
        with open(path, mode='w', encoding='utf-8') as outfile:
            yaml.dump(self.locals, outfile)
        self.log.warning(f"New file {path} written, please check for possible errors.")


async def migrate_3_10(self: Plugin):
    def _change_instance(instance: dict):
        if instance.get('afk_exemptions') and isinstance(instance['afk_exemptions'], list):
            instance['afk_exemptions'] = {
                "ucid": instance['afk_exemptions']
            }

    path = os.path.join(self.node.config_dir, 'plugins', self.plugin_name + '.yaml')
    if not os.path.exists(path):
        return
    data = yaml.load(Path(path).read_text(encoding='utf-8'))
    if self.node.name in data.keys():
        for name, node in data.items():
            if name == DEFAULT_TAG:
                _change_instance(node)
                continue
            for instance in node.values():
                _change_instance(instance)
    else:
        for instance in data.values():
            _change_instance(instance)
    with open(path, mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)


async def migrate_3_11(self: Plugin):
    def _change_instance(instance: dict):
        instance.pop('greeting_message_members', None)
        instance.pop('greeting_message_unmatched', None)
        instance.pop('smooth_pause', None)
        instance.pop('afk_exemptions', None)
        instance.pop('usage_alarm', None)
        # remove message_server_full if Slotblocking is not used
        if 'slotblocking' not in self.node.plugins:
            instance.pop('message_server_full', None)

    # first, re-organize the messages in servers.yaml
    server_config = os.path.join(self.node.config_dir, 'servers.yaml')
    server_data = yaml.load(Path(server_config).read_text(encoding='utf-8'))
    # make sure we have a default tag
    default = server_data.get(DEFAULT_TAG)
    if not default:
        default = server_data[DEFAULT_TAG] = {
            "messages": {
                'greeting_message_members': self.locals.get(DEFAULT_TAG, {}).get(
                    'greeting_message_members', '{player.name}, welcome back to {server.name}!'),
                'greeting_message_unmatched': self.locals.get(DEFAULT_TAG, {}).get(
                    'greeting_message_unmatched', '{player.name}, please use /linkme in our Discord, '
                                                  'if you want to see your user stats!'),
                'message_player_username': self.node.config.get('messages', {}).get(
                    'player_username', 'Your player name contains invalid characters. '
                                       'Please change your name to join our server.'),
                'message_player_default_username': self.node.config.get('messages', {}).get(
                    'player_default_username', 'Please change your default player name at the top right of the '
                                               'multiplayer selection list to an individual one!'),
                'message_ban': 'You are banned from this server. Reason: {}',
                'message_reserved': 'This server is locked for specific users.\n'
                                    'Please contact a server admin.',
                'message_no_voice': 'You need to be in voice channel "{}" to use this server!'
            }
        }
    else:
        default['messages'] = {
            'greeting_message_members': self.locals.get(DEFAULT_TAG, {}).get(
                'greeting_message_members', '{player.name}, welcome back to {server.name}!'),
            'greeting_message_unmatched': self.locals.get(DEFAULT_TAG, {}).get(
                'greeting_message_unmatched', '{player.name}, please use /linkme in our Discord, '
                                              'if you want to see your user stats!'),
            'message_player_username': self.node.config.get('messages', {}).get(
                'player_username', 'Your player name contains invalid characters. '
                                   'Please change your name to join our server.'),
            'message_player_default_username': self.node.config.get('messages', {}).get(
                'player_default_username', 'Please change your default player name at the top right of the '
                                           'multiplayer selection list to an individual one!'),
            'message_ban': server_data[DEFAULT_TAG].pop('message_ban', 'You are banned from this server. Reason: {}'),
            'message_reserved': server_data[DEFAULT_TAG].pop('message_reserved',
                                                             'This server is locked for specific users.\n'
                                                             'Please contact a server admin.'),
            'message_no_voice': server_data[DEFAULT_TAG].pop('message_no_voice',
                                                             'You need to be in voice channel "{}" to use this server!'),
        }
    if 'smooth_pause' in self.locals.get(DEFAULT_TAG, {}):
        default['smooth_pause'] = self.locals[DEFAULT_TAG].pop('smooth_pause')
    if self.locals.get(DEFAULT_TAG, {}).get('usage_alarm'):
        default['usage_alarm'] = self.locals[DEFAULT_TAG].pop('usage_alarm')
    default['slot_spamming'] = {
        "message": default.pop('message_slot_spamming', 'You have been kicked for slot spamming!'),
        "check_time": 5,
        "slot_changes": 5
    }
    for name, section in server_data.items():
        if name == DEFAULT_TAG:
            continue
        if 'messages' not in section:
            section['messages'] = {
                'greeting_message_members': default['messages']['greeting_message_members'],
                'greeting_message_unmatched': default['messages']['greeting_message_unmatched'],
                'message_player_username': default['messages']['message_player_username'],
                'message_player_default_username': default['messages']['message_player_default_username'],
                'message_ban': section.pop('message_ban', default['messages']['message_ban']),
                'message_reserved': section.pop('message_reserved', default['messages']['message_reserved']),
                'message_no_voice': section.pop('message_no_voice', default['messages']['message_no_voice']),
            }
        if 'afk_time' in section:
            section['afk'] = {
                'afk_time': section.pop('afk_time'),
                'message': section.pop('message_afk', default.get(
                    'message_afk', '{player.name}, you have been kicked for being AFK for more than {time}.'))
            }
            if self.locals.get(DEFAULT_TAG, {}).get('afk_exemptions'):
                section['afk']['exemptions'] = deepcopy(self.locals[DEFAULT_TAG]['afk_exemptions'])
    # remove defaults from the server sections
    for element in default.keys():
        for name, section in server_data.items():
            if name == DEFAULT_TAG:
                continue
            elif section.get(element) == default[element]:
                section.pop(element, None)
    default.pop('message_afk', None)
    # rewrite servers.yaml
    with open(server_config, mode='w', encoding='utf-8') as outfile:
        yaml.dump(server_data, outfile)
    # cleanup
    # remove messages from main.yaml
    config = os.path.join(self.node.config_dir, 'main.yaml')
    data = yaml.load(Path(config).read_text(encoding='utf-8'))
    if data.pop('messages', None):
        with open(config, mode='w', encoding='utf-8') as outfile:
            yaml.dump(data, outfile)
    # remove unnecessary stuff from own config
    path = os.path.join(self.node.config_dir, 'plugins', self.plugin_name + '.yaml')
    if not os.path.exists(path):
        return
    data = yaml.load(Path(path).read_text(encoding='utf-8'))
    if self.node.name in data.keys():
        for name, node in data.items():
            if name == DEFAULT_TAG:
                _change_instance(node)
                continue
            for instance in node.values():
                _change_instance(instance)
    else:
        for instance in data.values():
            _change_instance(instance)
    with open(path, mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)


async def migrate_3_12(self: Plugin):
    config = os.path.join(self.node.config_dir, 'main.yaml')
    data = yaml.load(Path(config).read_text(encoding='utf-8'))
    if not 'mission_rewrite' in data:
        return
    mission_rewrite = data.pop('mission_rewrite')
    with open(config, mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)
    if mission_rewrite is True:
        return
    config = os.path.join(self.node.config_dir, 'nodes.yaml')
    data = yaml.load(Path(config).read_text(encoding='utf-8'))
    for name, instance in data.get('instances', {}).items():
        instance['mission_rewrite'] = False
    with open(config, mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)
