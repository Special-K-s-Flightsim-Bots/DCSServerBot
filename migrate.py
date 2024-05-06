import core
import json
import os
import shutil
import traceback

from configparser import ConfigParser
from contextlib import suppress
from copy import deepcopy
from core import utils, DEFAULT_TAG, BACKUP_FOLDER
from extensions import TACVIEW_DEFAULT_DIR
from pathlib import Path
from typing import Union
from rich import print
from rich.prompt import IntPrompt, Confirm

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


def post_migrate_admin(node: str):
    def _migrate(_data: dict):
        remove = -1
        config = False
        for idx, download in enumerate(_data['downloads']):
            if download['label'] == 'DCSServerBot Logs':
                download['directory'] = 'logs'
                download['pattern'] = 'dcssb-*.log*'
            elif download['label'] == 'Config Files':
                config = True
                download['label'] = 'Main Config Files'
                download['pattern'] = '*.yaml'
            elif download['label'] == 'dcsserverbot.ini':
                remove = idx
            download['directory'] = download['directory'].replace('{server.installation}', '{server.instance.name}')
        if remove != -1:
            del _data['downloads'][remove]
        if config:
            _data['downloads'].append({
                "label": "Plugin Config Files",
                "directory": "./config/plugins",
                "pattern": "*.yaml"
            })
            _data['downloads'].append({
                "label": "Service Config Files",
                "directory": "./config/services",
                "pattern": "*.yaml"
            })

    with open('config/plugins/admin.yaml', mode='r', encoding='utf-8') as infile:
        data = yaml.load(infile)
    for element in data:
        if element == 'commands':
            continue
        elif element == DEFAULT_TAG:
            _migrate(data[DEFAULT_TAG])
        elif element == element:
            for name, instance in data[element].items():
                _migrate(instance)

    with open('config/plugins/admin.yaml', mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)


def post_migrate_music(node: str):
    def _migrate(_data: dict):
        _data['radios'] = {
            'Radio 1': deepcopy(_data['sink'])
        }
        _data['radios']['Radio 1']['type'] = 'SRSRadio'
        _data['radios']['Radio 1']['display_name'] = _data['radios']['Radio 1']['name']
        del _data['radios']['Radio 1']['name']
        del _data['sink']

    with open('config/plugins/music.yaml', mode='r', encoding='utf-8') as infile:
        data = yaml.load(infile)
    for element in data:
        if element == 'commands':
            continue
        elif element == DEFAULT_TAG:
            _migrate(data[DEFAULT_TAG])
        elif element == element:
            for name, instance in data[element].items():
                _migrate(instance)
    with open('config/plugins/music.yaml', mode='w', encoding='utf-8') as outfile:
        yaml.dump(data, outfile)


def post_migrate_greenieboard(node: str):
    with open('config/plugins/greenieboard.yaml', mode='r', encoding='utf-8') as infile:
        data = yaml.load(infile)
    # we only need to do a post migration is there were server specific settings
    if node not in data:
        return
    if os.path.exists('config/services/cleanup.yaml'):
        with open('config/services/cleanup.yaml', mode='r', encoding='utf-8') as infile:
            cleanups = yaml.load(infile)
    else:
        cleanups = {}
    cleanup = cleanups[node] = {}
    for name, instance in data[node].items():
        if 'Moose.AIRBOSS' in instance and instance['Moose.AIRBOSS'].get('delete_after'):
            if name not in cleanup:
                cleanup[name] = {}
            cleanup[name] |= {
                "Moose.AIRBOSS": {
                    "directory": os.path.join("{instance.home}", instance['Moose.AIRBOSS']['basedir']),
                    "pattern": "*.csv",
                    "delete_after": instance['Moose.AIRBOSS']['delete_after']
                }
            }
            del instance['Moose.AIRBOSS']['delete_after']
        elif 'FunkMan' in instance and instance['FunkMan'].get('delete_after'):
            if name not in cleanup:
                cleanup[name] = {}
            cleanup[name] |= {
                "FunkMan": {
                    "directory": os.path.join("{instance.home}", instance['FunkMan']['basedir']),
                    "pattern": "*.png",
                    "delete_after": instance['FunkMan']['delete_after']
                }
            }
            del instance['FunkMan']['delete_after']
    if cleanup:
        with open('config/services/cleanup.yaml', mode='w', encoding='utf-8') as outfile:
            yaml.dump(cleanups, outfile)
        with open('config/plugins/greenieboard.yaml', mode='w', encoding='utf-8') as outfile:
            yaml.dump(data, outfile)


def migrate(node: str):
    cfg = ConfigParser()
    cfg.read('config/default.ini', encoding='utf-8')
    cfg.read('config/dcsserverbot.ini', encoding='utf-8')
    master = cfg['BOT'].getboolean('MASTER')
    print("\n[blue]Thanks for using DCSServerBot 2!\nWe are now going to migrate you over to version 3.0.[/]\n")
    # check migration order
    if not master:
        if not os.path.exists('config/main.yaml'):
            print("[red]ATTENTION:[/]The Master Node needs to be migrated first! Aborting.")
            exit(-2)
        bot = yaml.load(Path('config/services/bot.yaml').read_text(encoding='utf-8'))
        single_admin = ('admin_channel' in bot)
    else:
        guild_id = IntPrompt.ask(
            'Please enter your Discord Guild ID (right click on your Discord server, "Copy Server ID")')
        if not Confirm.ask(
                "[red]ATTENTION:[/] Your database will be migrated to version 3.0. Do you want to continue?",
                default=False):
            exit(-2)
        single_admin = Confirm.ask(
            "Do you want a central admin channel for your servers (Y) or keep separate ones (N)?", default=False)
    print("Now, lean back and enjoy the migration...\n")

    try:
        if master:
            print("- [red]Master[/] node detected.")
        else:
            print("- [yellow]Agent[/] node detected.")
        # create backup directory
        os.makedirs(BACKUP_FOLDER.format(node), exist_ok=True)
        # Migrate all plugins
        os.makedirs('config/plugins', exist_ok=True)
        os.makedirs('config/services', exist_ok=True)
        plugins = [x.strip() for x in cfg['BOT']['PLUGINS'].split(',')]
        if 'OPT_PLUGINS' in cfg['BOT']:
            plugins.extend([x.strip() for x in cfg['BOT']['OPT_PLUGINS'].split(',')])
        for plugin_name in set(plugins):
            if os.path.exists(f'config/{plugin_name}.json'):
                core.Plugin.migrate_to_3(node, plugin_name)
                if plugin_name == 'admin':
                    post_migrate_admin(node)
                    print("- Migrated config/admin.json to config/plugins/admin.yaml")
                    continue
                elif plugin_name == 'music':
                    post_migrate_music(node)
                elif plugin_name == 'greenieboard':
                    post_migrate_greenieboard(node)
                if plugin_name in ['backup', 'ovgme', 'music']:
                    shutil.move(f'config/plugins/{plugin_name}.yaml', f'config/services/{plugin_name}.yaml')
                    print(f"- Migrated config/{plugin_name}.json to config/services/{plugin_name}.yaml")
                elif plugin_name == 'commands':
                    data = yaml.load(Path('config/plugins/commands.yaml').read_text(encoding='utf-8'))
                    data[DEFAULT_TAG] = {
                        "command_prefix": cfg['BOT']['COMMAND_PREFIX']
                    }
                    with open('config/plugins/commands.yaml', mode='w', encoding='utf-8') as out:
                        yaml.dump(data, out)
                    print("- Migrated config/commands.json to config/plugins/commands.yaml")
                else:
                    print(f"- Migrated config/{plugin_name}.json to config/plugins/{plugin_name}.yaml")

        if os.path.exists('config/plugins/scheduler.yaml'):
            all_schedulers = yaml.load(Path('config/plugins/scheduler.yaml').read_text(encoding='utf-8'))
        else:
            all_schedulers = {}
        scheduler = all_schedulers.get(node)
        if os.path.exists('config/presets.yaml'):
            presets = yaml.load(Path('config/presets.yaml').read_text(encoding='utf-8'))
        else:
            presets = {}
        if os.path.exists('config/plugins/userstats.yaml'):
            all_userstats = yaml.load(Path('config/plugins/userstats.yaml').read_text(encoding='utf-8'))
        else:
            all_userstats = {}
        userstats = all_userstats[node] = {}
        if os.path.exists('config/plugins/missionstats.yaml'):
            all_missionstats = yaml.load(Path('config/plugins/missionstats.yaml').read_text(encoding='utf-8'))
        else:
            all_missionstats = {}
        if node not in all_missionstats:
            all_missionstats[node] = {}
        missionstats = all_missionstats[node]
        # If we are not the first node to be migrated
        if os.path.exists('config/nodes.yaml'):
            nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
        else:
            nodes = {}
        nodes[node] = {
            "autoupdate": cfg['BOT'].getboolean('AUTOUPDATE')
        }
        if os.path.exists('config/servers.yaml'):
            servers = yaml.load(Path('config/servers.yaml').read_text(encoding='utf-8'))
        else:
            servers = {
                DEFAULT_TAG: {
                    "message_afk": cfg['DCS']['MESSAGE_AFK'],
                    "message_ban": cfg['DCS']['MESSAGE_BAN'],
                    'message_timeout': int(cfg['BOT']['MESSAGE_TIMEOUT']),
                    'message_server_full': cfg['DCS']['MESSAGE_SERVER_FULL']
                }
            }

        # main.yaml is only created on the Master node
        if master:
            main: dict[str, Union[int, str, list, dict]] = {
                "guild_id": guild_id,
                "use_dashboard": cfg['BOT'].getboolean('USE_DASHBOARD'),
                'chat_command_prefix': cfg['BOT']['CHAT_COMMAND_PREFIX'],
                "logging": {
                    "loglevel": cfg['LOGGING']['LOGLEVEL'],
                    "logrotate_count": int(cfg['LOGGING']['LOGROTATE_COUNT']),
                    "logrotate_size": int(cfg['LOGGING']['LOGROTATE_SIZE'])
                },
                "messages": {
                    "player_username": cfg['DCS']['MESSAGE_PLAYER_USERNAME'],
                    "player_default_username": cfg['DCS']['MESSAGE_PLAYER_DEFAULT_USERNAME']
                },
                "filter": {
                    "server_name": cfg['FILTER']['SERVER_FILTER'],
                    "mission_name": cfg['FILTER']['MISSION_FILTER']
                }
            }
            if 'TAG_FILTER' in cfg['FILTER']:
                main['filter']['tag'] = cfg['FILTER']['TAG_FILTER']
            if 'OPT_PLUGINS' in cfg['BOT']:
                main["opt_plugins"] = [x.strip() for x in cfg['BOT']['OPT_PLUGINS'].split(',')]
            bot = {
                'token': cfg['BOT']['TOKEN'],
                'owner': int(cfg['BOT']['OWNER']),
                'automatch': cfg['BOT'].getboolean('AUTOMATCH'),
                'autoban': cfg['BOT'].getboolean('AUTOBAN'),
                'message_ban': cfg['BOT']['MESSAGE_BAN'],
                'message_autodelete': int(cfg['BOT']['MESSAGE_AUTODELETE'])
            }
            # take the first admin channel as the single one
            if single_admin:
                for server_name, instance in utils.findDCSInstances():
                    if instance in cfg and 'ADMIN_CHANNEL' in cfg[instance]:
                        print(f"[yellow]- Configured ADMIN_CHANNEL of instance {instance} as single admin channel.[/]")
                        bot['admin_channel'] = int(cfg[instance]['ADMIN_CHANNEL'])
                        break

            if 'GREETING_DM' in cfg['BOT']:
                bot['greeting_dm'] = cfg['BOT']['GREETING_DM']
            if 'DISCORD_STATUS' in cfg['BOT']:
                bot['discord_status'] = cfg['BOT']['DISCORD_STATUS']
            if 'AUDIT_CHANNEL' in cfg['BOT']:
                bot['audit_channel'] = int(cfg['BOT']['AUDIT_CHANNEL'])
            bot['roles'] = {}
            for role in ['Admin', 'DCS Admin', 'DCS', 'GameMaster']:
                bot['roles'][role] = [x.strip() for x in cfg['ROLES'][role].split(',')]
            with open('config/services/bot.yaml', mode='w', encoding='utf-8') as out:
                yaml.dump(bot, out)
                print("- Created config/services/bot.yaml")

        if 'PUBLIC_IP' in cfg['BOT']:
            nodes[node]['public_ip'] = cfg['BOT']['PUBLIC_IP']
        nodes[node]['listen_address'] = '0.0.0.0' if cfg['BOT']['HOST'] == '127.0.0.1' else cfg['BOT']['HOST']
        nodes[node]['listen_port'] = int(cfg['BOT']['PORT'])
        nodes[node]['slow_system'] = cfg['BOT'].getboolean('SLOW_SYSTEM')
        nodes[node]['DCS'] = {
            "installation": cfg['DCS']['DCS_INSTALLATION'],
            "autoupdate": cfg['DCS'].getboolean('AUTOUPDATE'),
            "desanitize": cfg['BOT'].getboolean('DESANITIZE')
        }
        if 'DCS_USER' in cfg['DCS']:
            nodes[node]['DCS']['user'] = cfg['DCS']['DCS_USER']
            nodes[node]['DCS']['password'] = cfg['DCS']['DCS_PASSWORD']
        nodes[node]['database'] = {
            "url": cfg['BOT']['DATABASE_URL'],
            "pool_min": int(cfg['DB']['MASTER_POOL_MIN']),
            "pool_max": int(cfg['DB']['MASTER_POOL_MAX'])
        }
        # add missing configs to userstats
        if DEFAULT_TAG not in all_userstats:
            all_userstats[DEFAULT_TAG] = {}
        u = all_userstats[DEFAULT_TAG]
        u['greeting_message_members'] = cfg['DCS']['GREETING_MESSAGE_MEMBERS'].replace(
            '{}', '{player.name}', 1).replace('{}', '{server.name}')
        u['greeting_message_unmatched'] = cfg['DCS']['GREETING_MESSAGE_UNMATCHED'].replace('{name}', '{player.name}')
        u['wipe_stats_on_leave'] = cfg['BOT'].getboolean('WIPE_STATS_ON_LEAVE')

        nodes[node]['instances'] = {}
        for server_name, instance in utils.findDCSInstances():
            if instance in cfg:
                i = nodes[node]['instances'][instance] = {
                    "home": cfg[instance]['DCS_HOME'],
                    "bot_port": int(cfg[instance]['DCS_PORT']),
                    "max_hung_minutes": int(cfg['DCS']['MAX_HUNG_MINUTES'])
                }
                if 'MISSIONS_DIR' in cfg[instance]:
                    i['missions_dir'] = cfg[instance]['MISSIONS_DIR']
                if instance in scheduler:
                    schedule = scheduler[instance]
                    if 'affinity' in schedule:
                        nodes[node]['instances'][instance]['affinity'] = schedule['affinity']
                        del schedule['affinity']
                    if 'terrains' in schedule:
                        if 'extensions' not in schedule:
                            schedule['extensions'] = {}
                        schedule['extensions']['MizEdit'] = {
                            "terrains": schedule['terrains']
                        }
                        del schedule['terrains']
                    elif 'settings' in schedule:
                        if 'extensions' not in schedule:
                            schedule['extensions'] = {}
                        schedule['extensions']['MizEdit'] = {
                            "settings": schedule['settings']
                        }
                        del schedule['settings']
                    if 'extensions' in schedule:
                        i['extensions'] = schedule['extensions']
                        del schedule['extensions']
                    # unusual, but people might have done it
                    if 'presets' in schedule:
                        presets |= schedule['presets']
                        del schedule['presets']
                # fill missionstats
                m = missionstats[instance] = {}
                if 'EVENT_FILTER' in cfg['FILTER']:
                    m['filter'] = [x.strip() for x in cfg['FILTER']['EVENT_FILTER'].split(',')]
                m['enabled'] = cfg[instance].getboolean('MISSION_STATISTICS')
                m['display'] = cfg[instance].getboolean('DISPLAY_MISSION_STATISTICS')
                m['persistence'] = cfg[instance].getboolean('PERSIST_MISSION_STATISTICS')
                m['persist_ai_statistics'] = cfg[instance].getboolean('PERSIST_AI_STATISTICS')
                # create server config
                servers[server_name] = {
                    "server_user": cfg['DCS']['SERVER_USER'],
                    "afk_time": int(cfg['DCS']['AFK_TIME']),
                    "ping_admin_on_crash": cfg[instance].getboolean('PING_ADMIN_ON_CRASH'),
                    "autoscan": cfg[instance].getboolean('AUTOSCAN'),
                    "channels": {
                        "status": int(cfg[instance]['STATUS_CHANNEL']),
                        "chat": int(cfg[instance]['CHAT_CHANNEL'])
                    }
                }
                if not single_admin:
                    servers[server_name]['channels']['admin'] = int(cfg[instance]['ADMIN_CHANNEL'])
                if 'EVENTS_CHANNEL' in cfg[instance]:
                    servers[server_name]['channels']['events'] = int(cfg[instance]['EVENTS_CHANNEL'])
                if cfg[instance].getboolean('CHAT_LOG'):
                    servers[server_name]['chat_log'] = {
                        "count": int(cfg[instance]['CHAT_LOGROTATE_COUNT']),
                        "size": int(cfg[instance]['CHAT_LOGROTATE_SIZE'])
                    }
                if cfg[instance].getboolean('NO_COALITION_CHAT', fallback=False):
                    servers[server_name]['no_coalition_chat'] = True
                if cfg[instance].getboolean('COALITIONS'):
                    servers[server_name]['coalitions'] = {
                        "lock_time": cfg[instance]['COALITION_LOCK_TIME'],
                        "allow_players_pool": cfg[instance].getboolean('ALLOW_PLAYERS_POOL'),
                        "blue_role": cfg[instance]['Coalition Blue'],
                        "red_role": cfg[instance]['Coalition Red']
                    }
                    servers[server_name]['channels']['blue'] = int(cfg[instance]['COALITION_BLUE_CHANNEL'])
                    servers[server_name]['channels']['red'] = int(cfg[instance]['COALITION_RED_CHANNEL'])
                    if 'COALITION_BLUE_EVENTS' in cfg[instance]:
                        servers[server_name]['channels']['blue_events'] = int(cfg[instance]['COALITION_BLUE_EVENTS'])
                    if 'COALITION_RED_EVENTS' in cfg[instance]:
                        servers[server_name]['channels']['red_events'] = int(cfg[instance]['COALITION_RED_EVENTS'])
                if not cfg[instance].getboolean('STATISTICS'):
                    if instance not in userstats:
                        userstats[instance] = {}
                    userstats[instance]['enabled'] = False
        # add the extension defaults to the node config
        if DEFAULT_TAG in all_schedulers:
            schedule = all_schedulers[DEFAULT_TAG]
            if 'extensions' in schedule:
                nodes[node]['extensions'] = schedule['extensions']
                del schedule['extensions']
            # remove any presets from scheduler.yaml to a separate presets.yaml
            if 'presets' in schedule:
                if isinstance(schedule['presets'], dict):
                    presets |= schedule['presets']
                else:
                    with open(schedule['presets'], mode='r', encoding='utf-8') as pin:
                        presets |= json.load(pin)
                    shutil.move(schedule['presets'], BACKUP_FOLDER.format(node))
                del schedule['presets']

        # Now we need to figure if tacview has a delete_after configured...
        delete_after = nodes[node].get('extensions', {}).get('Tacview', {}).get('delete_after', 0)
        directory = nodes[node].get('extensions', {}).get('Tacview', {}).get('tacviewExportPath',
                                                                             TACVIEW_DEFAULT_DIR)
        if os.path.exists('config/services/cleanup.yaml'):
            with open('config/services/cleanup.yaml', mode='r', encoding='utf-8') as infile:
                cleanup = yaml.load(infile)
        else:
            cleanup = {}
        for name, instance in nodes[node].get('instances', {}).items():
            if 'extensions' in instance and 'Tacview' in instance['extensions']:
                _delete_after = instance['extensions']['Tacview'].get('delete_after', delete_after)
                _directory = instance['extensions']['Tacview'].get('tacviewExportPath', directory)
                if _delete_after:
                    if name not in cleanup:
                        cleanup[name] = {}
                    cleanup[name] |= {
                        "Tacview": {
                            "directory": _directory,
                            "pattern": "*.acmi",
                            "delete_after": _delete_after
                        }
                    }
                with suppress(KeyError):
                    del nodes[node]['instances'][name]['extensions']['Tacview']['delete_after']
        with suppress(KeyError):
            del nodes[node]['extensions']['Tacview']['delete_after']
        if cleanup:
            with open('config/services/cleanup.yaml', mode='w', encoding='utf-8') as outfile:
                yaml.dump(cleanup, outfile)

        # write main configuration
        if master:
            with open('config/main.yaml', mode='w', encoding='utf-8') as out:
                yaml.dump(main, out)
                print("- Created config/main.yaml")
        with open('config/nodes.yaml', mode='w', encoding='utf-8') as out:
            yaml.dump(nodes, out)
            print("- Created / updated config/nodes.yaml")
        with open('config/servers.yaml', mode='w', encoding='utf-8') as out:
            yaml.dump(servers, out)
            print("- Created / updated config/servers.yaml")
        # write plugin configuration
        if scheduler:
            with open('config/plugins/scheduler.yaml', mode='w', encoding='utf-8') as out:
                yaml.dump(all_schedulers, out)
                print("- Created / updated config/plugins/scheduler.yaml")
            if presets:
                with open('config/presets.yaml', mode='w', encoding='utf-8') as out:
                    yaml.dump(presets, out)
                print("- Created config/presets.yaml")
        if userstats:
            with open('config/plugins/userstats.yaml', mode='w', encoding='utf-8') as out:
                yaml.dump(all_userstats, out)
            print("- Created / updated config/plugins/missionstats.yaml")
        if missionstats:
            with open('config/plugins/missionstats.yaml', mode='w', encoding='utf-8') as out:
                yaml.dump(all_missionstats, out)
            print("- Created / updated config/plugins/missionstats.yaml")
        # shutil.move('config/default.ini', BACKUP_FOLDER)
        shutil.move('config/dcsserverbot.ini', BACKUP_FOLDER.format(node))
        print("\n[green]Migration to DCSServerBot 3.0 successful![/]\n\n")
        input("Press any key to launch.\n")
    except Exception:
        print("\n[red]Migration to DCSServerBot 3.0 failed![/]\n")
        traceback.print_exc()
        exit(-2)
