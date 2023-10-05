from copy import deepcopy

import core
import json
import os
import platform
import shutil
import traceback

from configparser import ConfigParser
from core import utils, DEFAULT_TAG, BACKUP_FOLDER
from pathlib import Path
from typing import Union
from rich import print
from rich.prompt import IntPrompt, Prompt

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


def post_migrate_admin():
    with open('config/plugins/admin.yaml') as infile:
        data = yaml.load(infile)
    config = False
    remove = -1
    for instance in data:
        if instance == 'commands':
            continue
        for idx, download in enumerate(data[instance]['downloads']):
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
            del data[instance]['downloads'][remove]
        if config:
            data[instance]['downloads'].append({
                "label": "Plugin Config Files",
                "directory": "./config/plugins",
                "pattern": "*.yaml"
            })
            data[instance]['downloads'].append({
                "label": "Service Config Files",
                "directory": "./config/services",
                "pattern": "*.yaml"
            })
    with open('config/plugins/admin.yaml', 'w') as outfile:
        yaml.dump(data, outfile)


def post_migrate_music():
    with open('config/plugins/music.yaml') as infile:
        data = yaml.load(infile)
    for name, instance in data.items():
        if name == 'commands':
            continue
        instance['radios'] = {
            'Radio 1': deepcopy(instance['sink'])
        }
        instance['radios']['Radio 1']['type'] = 'SRSRadio'
        instance['radios']['Radio 1']['display_name'] = instance['radios']['Radio 1']['name']
        del instance['radios']['Radio 1']['name']
        del instance['sink']
    with open('config/plugins/music.yaml', 'w') as outfile:
        yaml.dump(data, outfile)


def migrate():
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
    else:
        guild_id = IntPrompt.ask(
            'Please enter your Discord Guild ID (right click on your Discord server, "Copy Server ID")')
    # TODO: only for BETA testing!
    if 'dcsserverbot3' not in cfg['BOT']['DATABASE_URL']:
        yn = Prompt.ask(f"[red]ATTENTION:[/] Your DATABASE_URL is {cfg['BOT']['DATABASE_URL']}.\n"
                        f"This looks like a production migration. Do you want to continue?",
                        choices=['y', 'n'], default='n')
        if yn.lower() != 'y':
            exit(-2)
    single_admin = Prompt.ask(f"Do you want a central admin channel for your servers (Y) or keep separate ones (N)?",
                              choices=['y', 'n'], default='n') == 'y'
    print("Now, lean back and enjoy the migration...\n")

    try:
        if master:
            print("- [red]Master[/] node detected.")
        else:
            print("- [yellow]Agent[/] node detected.")
        # create backup directory
        os.makedirs(BACKUP_FOLDER, exist_ok=True)
        # Migrate all plugins
        os.makedirs('config/plugins', exist_ok=True)
        plugins = [x.strip() for x in cfg['BOT']['PLUGINS'].split(',')]
        if 'OPT_PLUGINS' in cfg['BOT']:
            plugins.extend([x.strip() for x in cfg['BOT']['OPT_PLUGINS'].split(',')])
        for plugin_name in set(plugins):
            if os.path.exists(f'config/{plugin_name}.json'):
                core.Plugin.migrate_to_3(plugin_name)
                if plugin_name == 'admin':
                    post_migrate_admin()
                    print(f"- Migrated config/admin.json to config/plugins/admin.yaml")
                    continue
                elif plugin_name == 'music':
                    post_migrate_music()
                if plugin_name in ['backup', 'ovgme', 'music']:
                    shutil.move(f'config/plugins/{plugin_name}.yaml', f'config/services/{plugin_name}.yaml')
                    print(f"- Migrated config/{plugin_name}.json to config/services/{plugin_name}.yaml")
                elif plugin_name == 'commands':
                    data = yaml.load(Path('config/plugins/commands.yaml').read_text(encoding='utf-8'))
                    data[DEFAULT_TAG] = {
                        "command_prefix": cfg['BOT']['COMMAND_PREFIX']
                    }
                    with open('config/plugins/commands.yaml', 'w', encoding='utf-8') as out:
                        yaml.dump(data, out)
                    print(f"- Migrated config/commands.json to config/plugins/commands.yaml")
                else:
                    print(f"- Migrated config/{plugin_name}.json to config/plugins/{plugin_name}.yaml")

        if os.path.exists('config/plugins/scheduler.yaml'):
            scheduler = yaml.load(Path('config/plugins/scheduler.yaml').read_text(encoding='utf-8'))
        else:
            scheduler = {}
        if os.path.exists('config/presets.yaml'):
            presets = yaml.load(Path('config/presets.yaml').read_text(encoding='utf-8'))
        else:
            presets = {}
        if os.path.exists('config/plugins/userstats.yaml'):
            userstats = yaml.load(Path('config/plugins/userstats.yaml').read_text(encoding='utf-8'))
        else:
            userstats = {}
        # If we are not the first node to be migrated
        if os.path.exists('config/nodes.yaml'):
            nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
        else:
            nodes = {}
        nodes[platform.node()] = {
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
                "database": {
                    "url": cfg['BOT']['DATABASE_URL'],
                    "pool_min": int(cfg['DB']['MASTER_POOL_MIN']),
                    "pool_max": int(cfg['DB']['MASTER_POOL_MAX'])
                },
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
                if 'backup' in main['opt_plugins']:
                    main['opt_plugins'].remove('backup')
            bot = {
                'token': cfg['BOT']['TOKEN'],
                'owner': int(cfg['BOT']['OWNER']),
                'automatch': cfg['BOT'].getboolean('AUTOMATCH'),
                'autoban': cfg['BOT'].getboolean('AUTOBAN'),
                'message_ban': cfg['BOT']['MESSAGE_BAN'],
                'message_autodelete': int(cfg['BOT']['MESSAGE_AUTODELETE']),
                "reports": {
                    "num_workers": int(cfg['REPORTS']['NUM_WORKERS'])
                }
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
            if 'CJK_FONT' in cfg['REPORTS']:
                bot['reports']['cjk_font'] = cfg['REPORTS']['CJK_FONT']
            if 'DISCORD_STATUS' in cfg['BOT']:
                bot['discord_status'] = cfg['BOT']['DISCORD_STATUS']
            if 'AUDIT_CHANNEL' in cfg['BOT']:
                bot['audit_channel'] = int(cfg['BOT']['AUDIT_CHANNEL'])
            bot['roles'] = {}
            for role in ['Admin', 'DCS Admin', 'DCS', 'GameMaster']:
                bot['roles'][role] = [x.strip() for x in cfg['ROLES'][role].split(',')]
            os.makedirs('config/services', exist_ok=True)
            with open('config/services/bot.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(bot, out)
                print("- Created config/services/bot.yaml")

        if 'PUBLIC_IP' in cfg['BOT']:
            nodes[platform.node()]['public_ip'] = cfg['BOT']['PUBLIC_IP']
        nodes[platform.node()]['listen_address'] = '0.0.0.0' if cfg['BOT']['HOST'] == '127.0.0.1' else cfg['BOT']['HOST']
        nodes[platform.node()]['listen_port'] = int(cfg['BOT']['PORT'])
        nodes[platform.node()]['slow_system'] = cfg['BOT'].getboolean('SLOW_SYSTEM')
        nodes[platform.node()]['DCS'] = {
            "installation": cfg['DCS']['DCS_INSTALLATION'],
            "autoupdate": cfg['DCS'].getboolean('AUTOUPDATE'),
            "desanitize": cfg['BOT'].getboolean('DESANITIZE')
        }
        if 'DCS_USER' in cfg['DCS']:
            nodes[platform.node()]['DCS']['dcs_user'] = cfg['DCS']['DCS_USER']
            nodes[platform.node()]['DCS']['dcs_password'] = cfg['DCS']['DCS_PASSWORD']
        # add missing configs to userstats
        if DEFAULT_TAG not in userstats:
            userstats[DEFAULT_TAG] = {}
        u = userstats[DEFAULT_TAG]
        u['greeting_message_members'] = cfg['DCS']['GREETING_MESSAGE_MEMBERS']
        u['greeting_message_unmatched'] = cfg['DCS']['GREETING_MESSAGE_UNMATCHED']
        u['wipe_stats_on_leave'] = cfg['BOT'].getboolean('WIPE_STATS_ON_LEAVE')

        nodes[platform.node()]['instances'] = {}
        missionstats = {}
        for server_name, instance in utils.findDCSInstances():
            if instance in cfg:
                i = nodes[platform.node()]['instances'][instance] = {
                    "home": cfg[instance]['DCS_HOME'],
                    "bot_port": int(cfg[instance]['DCS_PORT']),
                    "server": server_name,
                    "max_hung_minutes": int(cfg['DCS']['MAX_HUNG_MINUTES'])
                }
                if 'MISSIONS_DIR' in cfg[instance]:
                    i['missions_dir'] = cfg[instance]['MISSIONS_DIR']
                if instance in scheduler:
                    schedule = scheduler[instance]
                    if 'affinity' in schedule:
                        nodes[platform.node()]['instances'][instance]['affinity'] = schedule['affinity']
                        del schedule['affinity']
                    if 'settings' in schedule:
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
        if DEFAULT_TAG in scheduler:
            schedule = scheduler[DEFAULT_TAG]
            if 'extensions' in schedule:
                nodes[platform.node()]['extensions'] = schedule['extensions']
                del schedule['extensions']
            # remove any presets from scheduler.yaml to a separate presets.yaml
            if 'presets' in schedule:
                if isinstance(schedule['presets'], dict):
                    presets |= schedule['presets']
                else:
                    with open(schedule['presets'], 'r', encoding='utf-8') as pin:
                        presets |= json.load(pin)
                    shutil.move(schedule['presets'], BACKUP_FOLDER)
                del schedule['presets']

        # write main configuration
        if master:
            with open('config/main.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(main, out)
                print("- Created config/main.yaml")
        with open('config/nodes.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(nodes, out)
            print("- Created config/nodes.yaml")
        with open('config/servers.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(servers, out)
            print("- Created config/servers.yaml")
        # write plugin configuration
        if scheduler:
            with open('config/plugins/scheduler.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(scheduler, out)
                print("- Created config/plugins/scheduler.yaml")
            if presets:
                with open('config/presets.yaml', 'w', encoding='utf-8') as out:
                    yaml.dump(presets, out)
                print("- Created config/presets.yaml")
        if missionstats:
            with open('config/plugins/missionstats.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(missionstats, out)
            print("- Created config/plugins/missionstats.yaml")
        shutil.move('config/default.ini', BACKUP_FOLDER)
        shutil.move('config/dcsserverbot.ini', BACKUP_FOLDER)
        print("\n[green]Migration to DCSServerBot 3.0 successful, starting up ...[/]\n")
    except Exception:
        print("\n[red]Migration to DCSServerBot 3.0 failed![/]\n")
        traceback.print_exc()
        exit(-2)
