import os
import platform
import psycopg
import secrets
import shutil
import sys
if sys.platform == 'win32':
    import winreg

from contextlib import closing, suppress
from core import utils, SAVED_GAMES
from pathlib import Path
from rich import print
from rich.prompt import IntPrompt, Prompt
from typing import Optional, Tuple
from urllib.parse import quote

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

DCSSB_DB_USER = "dcsserverbot"
DCSSB_DB_NAME = "dcsserverbot"


class InvalidParameter(Exception):
    def __init__(self, section: str, parameter: str, error: Optional[str] = None):
        if error:
            super().__init__(f"Section [{section}] has an invalid value for parameter \"{parameter}\": {error}")
        else:
            super().__init__(f"Section [{section}] has an invalid value for parameter \"{parameter}\".")


class MissingParameter(Exception):
    def __init__(self, section: str, parameter: str, error: Optional[str] = None):
        if error:
            super().__init__(f"Parameter \"{parameter}\" missing in section [{section}]: {error}")
        else:
            super().__init__(f"Parameter \"{parameter}\" missing in section [{section}]")


class Install:

    @staticmethod
    def get_dcs_installation_linux() -> Optional[str]:
        dcs_installation = None
        while dcs_installation is None:
            dcs_installation = Prompt.ask(prompt="Please enter the path to your DCS World installation")
            if not os.path.exists(dcs_installation):
                print("Directory not found. Please try again.")
                dcs_installation = None
        return dcs_installation

    @staticmethod
    def get_dcs_installation_win32() -> Optional[str]:
        print("\nSearching for DCS installations ...")
        key = skey = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics", 0)
            num_dcs_installs = winreg.QueryInfoKey(key)[0]
            if num_dcs_installs == 0:
                raise FileNotFoundError
            installs = list[Tuple[str, str]]()
            for i in range(0, num_dcs_installs):
                name = winreg.EnumKey(key, i)
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{name}", 0)
                path = winreg.QueryValueEx(skey, 'Path')[0]
                if os.path.exists(path):
                    installs.append((name, path))
            if len(installs) == 0:
                raise FileNotFoundError
            elif len(installs) == 1:
                print(f"[green]- {installs[0][0]} found.[/]")
                return installs[0][1]
            else:
                print('I\'ve found multiple installations of DCS World on this PC:')
                for i in range(0, len(installs)):
                    print(f'{i+1}: {installs[i][0]}')
                num = IntPrompt.ask(prompt='Please specify, which installation you want the bot to use',
                                    choices=[str(x) for x in range(1, len(installs) + 1)],
                                    show_choices=True)
                return installs[num-1][1]
        except (FileNotFoundError, OSError):
            return Install.get_dcs_installation_linux()
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()

    @staticmethod
    def get_database_url() -> Optional[str]:
        host = '127.0.0.1'
        port = 5432
        if not utils.is_open(host, port):
            print(f'[red]No PostgreSQL-database found on {host}:{port}![/]')
            host = Prompt.ask("Enter the hostname of your PostgreSQL-database", default='127.0.0.1')
            while not utils.is_open(host, port):
                port = IntPrompt.ask(prompt='Enter the port to your PostgreSQL-database', default=5432)
        while True:
            passwd = Prompt.ask('Please enter your PostgreSQL master password (user=postgres)', password=True)
            url = f'postgres://postgres:{quote(passwd)}@{host}:{port}/postgres'
            with psycopg.connect(url, autocommit=True) as conn:
                with closing(conn.cursor()) as cursor:
                    passwd = secrets.token_urlsafe(8)
                    try:
                        cursor.execute(f"CREATE USER {DCSSB_DB_USER} WITH ENCRYPTED PASSWORD '{passwd}'")
                    except psycopg.Error:
                        print(f'[yellow]Existing {DCSSB_DB_USER} user found![/]')
                        while True:
                            passwd = Prompt.ask(f"Please enter your password for user '{DCSSB_DB_USER}'",
                                                password=True)
                            try:
                                with psycopg.connect(
                                        f"postgres://{DCSSB_DB_USER}:{quote(passwd)}@{host}:{port}/{DCSSB_DB_NAME}"):
                                    pass
                                break
                            except psycopg.Error:
                                print("[red]Wrong password! Try again.[/]")
                    with suppress(psycopg.Error):
                        cursor.execute(f"CREATE DATABASE {DCSSB_DB_NAME}")
                        cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {DCSSB_DB_NAME} TO {DCSSB_DB_USER}")
                        cursor.execute(f"ALTER DATABASE {DCSSB_DB_NAME} OWNER TO {DCSSB_DB_USER}")
                    print("[green]- Database user and database created.[/]")
                    return f"postgres://{DCSSB_DB_USER}:{quote(passwd)}@{host}:{port}/{DCSSB_DB_NAME}"

    @staticmethod
    def install_master() -> Tuple[dict, dict, dict]:
        print("\n1. Database Setup")
        database_url = Install.get_database_url()
        print("\n2. Discord Setup")
        guild_id = IntPrompt.ask(
            'Please enter your Discord Guild ID (right click on your Discord server, "Copy Server ID")')
        main = {
            "guild_id": guild_id,
            "database": {
                "url": database_url
            }
        }
        token = Prompt.ask('Please enter your discord TOKEN (see documentation)', password=True) or '<see documentation>'
        owner = Prompt.ask('Please enter your Owner ID (right click on your discord user, "Copy User ID")')
        print("""
We now need to setup your Discord roles and channels.
DCSServerBot creates a role mapping for your bot users. It has the following internal roles:
        """)
        print({
            "Admin": "Users can delete data, change the bot, run commands on your server",
            "DCS Admin": "Users can upload missions, start/stop DCS servers, kick/ban users, etc.",
            "DCS": "Normal user, can pull statistics, ATIS, etc."
        })
        print("""
Please separate roles by comma, if you want to provide more than one.
You can keep the defaults, if unsure and create the respective roles in your Discord server.
        """)
        roles = {
            "Admin": Prompt.ask("Which role(s) in your discord should hold the [bold]Admin[/] role?",
                                default="Admin").split(','),
            "DCS Admin": Prompt.ask("Which role(s) in your discord should hold the [bold]DCS Admin[/] role?",
                                    default="DCS Admin").split(','),
            "DCS": Prompt.ask("Which role(s) in your discord should hold the [bold]DCS[/] role?",
                              default="@everyone").split(',')
        }
        bot = {
            "token": token,
            "owner": owner,
            "roles": roles
        }
        audit_channel = IntPrompt.ask("\nPlease provide a channel ID for audit events (optional) ", default=-1)
        admin_channel = IntPrompt.ask("\nThe bot can either use a dedicated admin channel for each server or a central "
                                      "admin channel for all servers.\n"
                                      "If you want to use a central one, please provide the ID (optional)", default=-1)
        if audit_channel and audit_channel != -1:
            bot['audit_channel'] = audit_channel
        if admin_channel and admin_channel != -1:
            bot['admin_channel'] = admin_channel
        nodes = {}
        return main, nodes, bot

    @staticmethod
    def install():
        print("""
[bright_blue]Hello! Thank you for choosing DCSServerBot.[/]
DCSServerBot supports everything from single server installations to huge server farms with multiple servers across 
the planet.

I will now guide you through the installation process.
If you need any further assistance, please visit the support discord, listed in the documentation.

For a successful installation, you need to fulfill the following prerequisites:

    1. Installation of PostgreSQL
    2. A Discord TOKEN for your bot from https://discord.com/developers/applications
    3. Git for Windows (optional but recommended)
                """)
        if int(platform.python_version_tuple()[1]) == 9:
            print("[yellow]Your Python 3.9 installation is outdated, you should upgrade it to 3.10 or higher![/]\n")
        print("""
If you have installed Git for Windows, I'd recommend that you install the bot using

    [italic][bright_black]git clone https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot.git[/][/]

        """)
        if Prompt.ask(prompt="Have you fulfilled all these requirements", choices=['y', 'n'], show_choices=True,
                      default='n') == 'n':
            print("Aborting.")
            exit(-1)

        if not os.path.exists('config/main.yaml'):
            main, nodes, bot = Install.install_master()
            master = True
        else:
            main = yaml.load(Path('config/main.yaml').read_text(encoding='utf-8'))
            nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
            bot = yaml.load(Path('config/services/bot.yaml').read_text(encoding='utf-8'))
            if platform.node() in nodes:
                if Prompt.ask("[red]A configuration for this nodes exists already![/]\n"
                              "Do you want to overwrite it?", choices=['y', 'n'], default='n') == 'n':
                    print("Aborted.")
                    exit(-1)
            else:
                print("[yellow]Configuration found, adding another node...[/]")
            master = False

        print(f"\n3. Node Setup")
        if sys.platform == 'win32':
            dcs_installation = Install.get_dcs_installation_win32() or '<see documentation>'
        else:
            dcs_installation = Install.get_dcs_installation_linux()
        node = nodes[platform.node()] = {
            "DCS": {
                "installation": dcs_installation
            }
        }
        if Prompt.ask("Do you want your DCS installation being auto-updated by the bot?", choices=['y', 'n'],
                      default='y') == 'y':
            node["DCS"]["autoupdate"] = True
            print("[green]- autoupdate enabled for DCS[/]")
        # Check for SRS
        srs_path = os.path.expandvars('%ProgramFiles%\\DCS-SimpleRadio-Standalone')
        if not os.path.exists(srs_path):
            srs_path = Prompt.ask("Please enter the path to your DCS-SRS installation.\n"
                                  "Press ENTER, if there is none.")
        if srs_path:
            node['extensions'] = {
                'SRS': {
                    'installation': srs_path
                }
            }
        # check if we can enable autoupdate
        try:
            import git
            node['autoupdate'] = True
            print("[green]- autoupdate enabled for DCSServerBot[/]")
        except ImportError:
            pass

        print(f"\n4. DCS Server Setup")
        servers = {}
        scheduler = {}
        node['instances'] = {}
        bot_port = 6666
        srs_port = 5002
        for name, instance in utils.findDCSInstances():
            if Prompt.ask(f'\nDCS server "{name}" found.\n'
                          'Would you like to manage this server through DCSServerBot?)',
                          choices=['y', 'n'], show_choices=True, default='y') == 'y':
                node['instances'][instance] = {
                    "bot_port": bot_port,
                    "home": os.path.join(SAVED_GAMES, instance),
                    "server": name
                }
                if srs_path:
                    srs_config = f"%USERPROFILE%\\Saved Games\\{instance}\\Config\\SRS.cfg"
                    node['instances'][instance]['extensions'] = {
                        "SRS": {
                            "config": srs_config,
                            "port": srs_port
                        }
                    }
                    if not os.path.exists(os.path.expandvars(srs_config)):
                        if os.path.exists(os.path.join(srs_path, "server.cfg")):
                            shutil.copy2(os.path.join(srs_path, "server.cfg"), srs_config)
                        else:
                            print("[red]SRS configuration could not be created.\n"
                                  f"Please copy your server.cfg to {srs_config} manually.[/]")
                bot_port += 1
                srs_port += 2
                print("DCSServerBot needs up to 3 channels per supported server:")
                print({
                    "Status Channel": "To display the mission and player status.",
                    "Chat Channel": "[bright_black]Optional:[/]: An in-game chat replication.",
                    "Admin Channel": "[bright_black]Optional:[/] For admin commands. Only needed, "
                                     "if no central admin channel is set."
                })
                print("""
The Status Channel should be readable by everyone and only writable by the bot.
The Chat Channel should be readable and writable by everyone.
The Admin channel - if provided - should only be readable and writable by Admin and DCS Admin users.

You can create these channels now, as I will ask for the IDs in a bit. 
DCSServerBot needs the following permissions on them to work:

    - View Channel
    - Send Messages
    - Read Messages
    - Read Message History
    - Add Reactions
    - Attach Files
    - Embed Links
    - Manage Messages
                """)

                servers[name] = {
                    "channels": {
                        "status": IntPrompt.ask("Please enter the ID of your [bold]Status Channel[/]"),
                        "chat": IntPrompt.ask("Please enter the ID of your [bold]Chat Channel[/] (optional)",
                                              default=-1)
                    }
                }
                if 'admin_channel' not in bot:
                    servers[name]['channels']['admin'] = IntPrompt.ask("Please enter the ID of your admin channel")
                if Prompt.ask("Do you want DCSServerBot to autostart this server?", choices=['y', 'n'],
                              default='y') == 'y':
                    scheduler[instance] = {
                        "schedule": {
                            "00-24": "YYYYYYY"
                        }
                    }
                else:
                    scheduler[instance] = {}
        print("\n\nAll set. Writing / updating your config files now...")
        if master:
            with open('config/main.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(main, out)
                print("- Created config/main.yaml")
            os.makedirs('config/services', exist_ok=True)
            with open('config/services/bot.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(bot, out)
                print("- Created config/services/bot.yaml")
        with open('config/nodes.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(nodes, out)
            print("- Created config/nodes.yaml")
        with open('config/servers.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(servers, out)
            print("- Created config/servers.yaml")
        # write plugin configuration
        if scheduler:
            os.makedirs('config/plugins', exist_ok=True)
            with open('config/plugins/scheduler.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(scheduler, out)
                print("- Created config/plugins/scheduler.yaml")
        print("""
[green]Your basic DCSServerBot configuration is finished.[/]
 
You can now review the created configuration files below your config folder of your DCSServerBot-installation.
There is much more to explore and to configure, so please don't forget to have a look at the documentation!

You can start DCSServerBot with:

    [bright_black]run.cmd[/]
        """)


if __name__ == "__main__":
    try:
        Install.install()
    except KeyboardInterrupt:
        print("\nAborted.")
