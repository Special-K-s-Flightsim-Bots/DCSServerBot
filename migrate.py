import core
import json
import os
import platform
import shutil
import yaml
from configparser import ConfigParser
from core import utils, DEFAULT_TAG
from pathlib import Path
from typing import Union
from rich.prompt import IntPrompt


def migrate():
    print("Thanks for using DCSServerBot 2.x. We are now going to migrate you over to version 3.0!")
    guild_id = IntPrompt.ask("First of all, we need your Discord Guild ID")
    print("Now, lean back and enjoy the migration...")
    cfg = ConfigParser()
    cfg.read('config/default.ini', encoding='utf-8')
    cfg.read('config/dcsserverbot.ini', encoding='utf-8')
    # Migrate all plugins
    os.makedirs('config/plugins', exist_ok=True)
    plugins = [x.strip() for x in cfg['BOT']['PLUGINS'].split(',')]
    plugins.extend([x.strip() for x in cfg['BOT']['OPT_PLUGINS'].split(',')])
    for plugin_name in set(plugins):
        if os.path.exists(f'config/{plugin_name}.json'):
            if plugin_name == 'admin':
                shutil.move(f'config/admin.json', './config/backup')
                print(" - NOT migrated config/admin.json, falling back to default instead.")
                continue
            core.Plugin.migrate_to_3(plugin_name)
            print(f"- Migrated config/{plugin_name}.json to config/plugins/{plugin_name}.yaml")

    if os.path.exists('config/plugins/scheduler.yaml'):
        scheduler = yaml.safe_load(Path('config/plugins/scheduler.yaml').read_text())
    else:
        scheduler = {}
    if os.path.exists('config/plugins/userstats.yaml'):
        userstats = yaml.safe_load(Path('config/plugins/userstats.yaml').read_text())
    else:
        userstats = {}
    # If we are not the first node to be migrated
    if os.path.exists('config/nodes.yaml'):
        nodes = yaml.safe_load(Path('config/nodes.yaml').read_text())
    else:
        nodes = {}
    nodes[platform.node()] = {}
    servers = {
        DEFAULT_TAG: {
            "message_afk": cfg['DCS']['MESSAGE_AFK'],
            'message_timeout': int(cfg['BOT']['MESSAGE_TIMEOUT'])
        }
    }
    main: dict[str, Union[int, str, list, dict]] = {
        "guild_id": guild_id,
        "autoupdate": cfg['BOT'].getboolean('AUTOUPDATE'),
        "use_dashboard": cfg['BOT'].getboolean('USE_DASHBOARD'),
        "database": {
            "url": cfg['BOT']['DATABASE_URL'],
            "pool_min": int(cfg['DB']['MASTER_POOL_MIN']),
            "pool_max": int(cfg['DB']['MASTER_POOL_MAX'])
        },
        "logging": {
            "loglevel": cfg['LOGGING']['LOGLEVEL'],
            "logrotate_count": int(cfg['LOGGING']['LOGROTATE_COUNT']),
            "logrotate_size": int(cfg['LOGGING']['LOGROTATE_SIZE'])
        }
    }
    if 'OPT_PLUGINS' in cfg['BOT']:
        main["opt_plugins"] = [x.strip() for x in cfg['BOT']['OPT_PLUGINS'].split(',')]
        if 'backup' in main['opt_plugins']:
            main['opt_plugins'].remove('backup')
    if cfg['BOT'].getboolean('MASTER'):
        bot = {
            'token': cfg['BOT']['TOKEN'],
            'owner': int(cfg['BOT']['OWNER']),
            'chat_command_prefix': cfg['BOT']['CHAT_COMMAND_PREFIX'],
            'automatch': cfg['BOT'].getboolean('AUTOMATCH'),
            'autoban': cfg['BOT'].getboolean('AUTOBAN'),
            'message_ban': cfg['DCS']['MESSAGE_BAN'],
            'message_autodelete': int(cfg['BOT']['MESSAGE_AUTODELETE']),
            "reports": {
                "num_workers": int(cfg['REPORTS']['NUM_WORKERS'])
            },
            "filter": {
                "server_name": cfg['FILTER']['SERVER_FILTER'],
                "mission_name": cfg['FILTER']['MISSION_FILTER']
            }
        }
        if 'GREETING_DM' in cfg['BOT']:
            bot['greeting_dm'] = cfg['BOT']['GREETING_DM']
        if 'CJK_FONT' in cfg['REPORTS']:
            bot['reports']['cjk_font'] = cfg['REPORTS']['CJK_FONT']
        if 'TAG_FILTER' in cfg['FILTER']:
            bot['filter']['tag'] = cfg['FILTER']['TAG_FILTER']
        if 'DISCORD_STATUS' in cfg['BOT']:
            bot['discord_status'] = cfg['BOT']['DISCORD_STATUS']
        if 'AUDIT_CHANNEL' in cfg['BOT']:
            bot['audit_channel'] = int(cfg['BOT']['AUDIT_CHANNEL'])
        bot['roles'] = {}
        for role in ['Admin', 'DCS Admin', 'DCS', 'GameMaster']:
            bot['roles'][role] = [x.strip() for x in cfg['ROLES'][role].split(',')]
        os.makedirs('config/services', exist_ok=True)
        with open('config/services/bot.yaml', 'w') as out:
            yaml.safe_dump(bot, out)
            print("- Created config/services/bot.yaml")
    if 'PUBLIC_IP' in cfg['BOT']:
        nodes[platform.node()]['public_ip'] = cfg['BOT']['PUBLIC_IP']
    nodes[platform.node()]['listen_address'] = '0.0.0.0' if cfg['BOT']['HOST'] == '127.0.0.1' else cfg['BOT']['HOST']
    nodes[platform.node()]['listen_port'] = int(cfg['BOT']['PORT'])
    nodes[platform.node()]['slow_system'] = cfg['BOT'].getboolean('SLOW_SYSTEM')
    nodes[platform.node()]['DCS'] = {
        "installation": cfg['DCS']['DCS_INSTALLATION'],
        "autoupdate": cfg['DCS'].getboolean('AUTOUPDATE'),
        "desanitize": cfg['BOT'].getboolean('DESANITIZE'),
        "max_hung_minutes": int(cfg['DCS']['MAX_HUNG_MINUTES'])
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
                "bot_port": int(cfg[instance]['DCS_PORT']),
                "server": server_name,
                "server_user": cfg['DCS']['SERVER_USER'],
            }
            if 'MISSIONS_DIR' in cfg[instance]:
                i['missions_dir'] = cfg[instance]['MISSIONS_DIR']
            if instance in scheduler:
                schedule = scheduler[instance]
                if 'extensions' in schedule:
                    i['extensions'] = schedule['extensions']
                    del schedule['extensions']
                if 'affinity' in schedule:
                    nodes[platform.node()]['instances'][instance]['affinity'] = schedule['affinity']
                    del schedule['affinity']
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
                "afk_time": int(cfg['DCS']['AFK_TIME']),
                "ping_admin_on_crash": cfg[instance].getboolean('PING_ADMIN_ON_CRASH'),
                "channels": {
                    "admin": int(cfg[instance]['STATUS_CHANNEL']),
                    "status": int(cfg[instance]['STATUS_CHANNEL']),
                    "chat": int(cfg[instance]['CHAT_CHANNEL'])
                }
            }
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
                    "blue_role": [x.strip() for x in cfg[instance]['Coalition Blue'].split(',')],
                    "red_role": [x.strip() for x in cfg[instance]['Coalition Red'].split(',')]
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
                presets = schedule['presets']
            else:
                with open(schedule['presets'], 'r') as pin:
                    presets = json.load(pin)
                os.makedirs('config/backup', exist_ok=True)
                shutil.move(schedule['presets'], 'config/backup')
            with open('config/presets.yaml', 'w') as pout:
                yaml.safe_dump(presets, pout)
            del schedule['presets']

    # write main configuration
    with open('config/main.yaml', 'w') as out:
        yaml.safe_dump(main, out)
        print("- Created config/main.yaml")
    with open('config/nodes.yaml', 'w') as out:
        yaml.safe_dump(nodes, out)
        print("- Created config/nodes.yaml")
    with open('config/servers.yaml', 'w') as out:
        yaml.safe_dump(servers, out)
        print("- Created config/servers.yaml")
    # write plugin configuration
    if scheduler:
        with open('config/plugins/scheduler.yaml', 'w') as out:
            yaml.safe_dump(scheduler, out)
    if missionstats:
        with open('config/plugins/missionstats.yaml', 'w') as out:
            yaml.safe_dump(missionstats, out)
        print("- Created config/plugins/missionstats.yaml")
    shutil.move('config/default.ini', 'config/backup')
    shutil.move('config/dcsserverbot.ini', 'config/backup')
    print("Migration to DCSServerBot 3.0 finished.")
