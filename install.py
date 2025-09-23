import asyncio
import logging
import os
import platform
import psycopg
import random
import secrets
import shutil
import stat
import sys

if sys.platform == 'win32':
    import winreg

from contextlib import closing, suppress
from core import utils, SAVED_GAMES, translations, COMMAND_LINE_ARGS
from pathlib import Path
from rich import print
from rich.console import Console
from rich.prompt import IntPrompt, Prompt, Confirm
from typing import Optional, Callable, Any
from urllib.parse import quote, urlparse

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML(typ='safe')

# for gettext // i18n
_: Optional[Callable[[str], str]] = None


class Install:

    def __init__(self, node: str):
        self.node = node
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.log.propagate = False
        self.log.setLevel(logging.DEBUG)
        self.use_upnp = utils.is_upnp_available()
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        os.makedirs('logs', exist_ok=True)
        fh = logging.FileHandler(os.path.join('logs', f'{self.node}-install.log'), encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        self.log.addHandler(fh)
        self.log.info("Installation started.")

    @staticmethod
    def get_dcs_installation_linux() -> Optional[str]:
        global _

        dcs_installation = None
        while dcs_installation is None:
            dcs_installation = Prompt.ask(prompt=_("Please enter the path to your DCS World installation"))
            if not dcs_installation:
                if Confirm.ask(_("Do you want to continue without a DCS installation being set?"), default=False):
                    return None
            elif not os.path.exists(dcs_installation):
                print(_("Directory not found. Please try again."))
                dcs_installation = None
        return dcs_installation

    @staticmethod
    def get_dcs_installation_win32() -> Optional[str]:
        global _

        print(_("Searching for DCS installations ..."))
        key = skey = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics", 0)
            num_dcs_installs = winreg.QueryInfoKey(key)[0]
            if num_dcs_installs == 0:
                raise FileNotFoundError
            installs = list[tuple[str, str]]()
            for i in range(0, num_dcs_installs):
                name = winreg.EnumKey(key, i)
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{name}", 0)
                path = winreg.QueryValueEx(skey, 'Path')[0]
                if os.path.exists(path):
                    installs.append((name, path))
            if len(installs) == 0:
                raise FileNotFoundError
            else:
                installs.append((_("Other"), ""))
                for i in range(0, len(installs)):
                    print(f'{i+1}: {installs[i][0]}')
                num = IntPrompt.ask(prompt=_('Please specify, which installation you want the bot to use.\n'
                                             'Chose "Other", if it is not in the list'),
                                    choices=[str(x) for x in range(1, len(installs) + 1)],
                                    show_choices=True)
                path = installs[num-1][1]
                if not path:
                    raise FileNotFoundError
                return path
        except (FileNotFoundError, OSError):
            return Install.get_dcs_installation_linux()
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()

    @staticmethod
    def get_database_host(host: str = '127.0.0.1', port: int = 5432) -> Optional[tuple[str, int]]:
        if not utils.is_open(host, port):
            print(_('[red]No PostgreSQL-database found on {host}:{port}![/]').format(host=host, port=port))
            host = Prompt.ask(_("Enter the hostname of your PostgreSQL-database"), default='127.0.0.1')
            while not utils.is_open(host, port):
                port = IntPrompt.ask(prompt=_('Enter the port to your PostgreSQL-database'), default=5432)
        return host, port

    @staticmethod
    def get_database_url(user: str, database: str) -> Optional[str]:
        host, port = Install.get_database_host('127.0.0.1', 5432)
        while True:
            master_db = Prompt.ask(_('Please enter the name of your PostgreSQL master database'), default='postgres')
            master_user = Prompt.ask(_('Please enter your PostgreSQL master user name'), default='postgres')
            master_passwd = Prompt.ask(_('Please enter your PostgreSQL master password (user={})').format(master_user))
            url = f'postgres://{master_user}:{quote(master_passwd)}@{host}:{port}/{master_db}?sslmode=prefer'
            try:
                with psycopg.connect(url, autocommit=True) as conn:
                    with closing(conn.cursor()) as cursor:
                        try:
                            passwd = utils.get_password('database') or ''
                        except ValueError:
                            passwd = secrets.token_urlsafe(8)
                        try:
                            cursor.execute(f"CREATE USER {user} WITH ENCRYPTED PASSWORD '{passwd}'")
                        except psycopg.Error:
                            print(_('[yellow]Existing {} user found![/]').format(user))
                            for i in range(1, 4):
                                passwd = Prompt.ask(
                                    _("Please enter your password for user {}").format(user))
                                try:
                                    with psycopg.connect(f"postgres://{user}:{quote(passwd)}@{host}:{port}/{database}?sslmode=prefer"):
                                        pass
                                    break
                                except psycopg.Error:
                                    print(_("[red]Wrong password! Try again ({}/3).[/]").format(i+1))
                            else:
                                print(_('[yellow]You have entered 3x a wrong password. I have reset it.[/]'))
                                passwd = secrets.token_urlsafe(8)
                                cursor.execute(f"ALTER USER {user} WITH ENCRYPTED PASSWORD '{passwd}'")
                        # store the password
                        utils.set_password('database', passwd)
                        with suppress(psycopg.Error):
                            cursor.execute(f"CREATE DATABASE {database}")
                            cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {database} TO {user}")
                            cursor.execute(f"ALTER DATABASE {database} OWNER TO {user}")
                        print(_("[green]Database user and database created.[/]"))
                    return f"postgres://{user}:SECRET@{host}:{port}/{database}?sslmode=prefer"
            except psycopg.OperationalError:
                print(_("[red]Master password wrong. Please try again.[/]"))

    def install_master(self) -> tuple[dict, dict, dict]:
        global _

        def get_supported_languages():
            return ['EN'] + [
                name.upper() for name in os.listdir('locale')
                if os.path.isdir(os.path.join('locale', name))
            ]

        def configure_discord() -> tuple[dict, dict]:
            print(_("\n[u]2. Discord Setup[/]"))
            guild_id = IntPrompt.ask(
                _('Please enter your Discord Guild ID (right click on your Discord server, "Copy Server ID")'))
            main: dict[str, Any] = {
                "guild_id": guild_id,
                "autoupdate": autoupdate
            }
            if use_lang_in_game:
                main['language'] = translations.get_language()
            token = Prompt.ask(_('Please enter your discord TOKEN (see documentation)')) or '<see documentation>'
            utils.set_password('token', token)
            owner = IntPrompt.ask(_('Please enter your Owner ID (right click on your discord user, "Copy User ID")'))
            print(_("\nWe now need to setup your Discord roles and channels.\n"
                    "DCSServerBot creates a role mapping for your bot users. It has the following internal roles:"))
            print({
                "Admin": _("Users can delete data, change the bot, run commands on your server"),
                "DCS Admin": _("Users can upload missions, start/stop DCS servers, kick/ban users, etc."),
                "DCS": _("Normal user, can pull statistics, ATIS, etc.")
            })
            print(_("Please separate roles by comma, if you want to provide more than one.\n"
                    "You can keep the defaults, if unsure and create the respective roles in your Discord server."))

            role_names = ["Admin", "DCS Admin", "DCS"]
            defaults = ["Admin", "DCS Admin", "@everyone"]
            roles = {}
            for role_name, default in zip(role_names, defaults):
                _roles = Prompt.ask(
                    _("Which role(s) in your discord should hold the [bold]{}[/] role?").format(role_name),
                    default=default).split(',')
                # Convert to integer where possible
                _roles = [int(item) if item.isdigit() else item for item in _roles]
                roles[role_name] = _roles

            bot = {
                "owner": owner,
                "roles": roles
            }
            audit_channel = IntPrompt.ask(_("\nPlease provide a channel ID for audit events (optional)"), default=-1)
            admin_channel = IntPrompt.ask(_("\nThe bot can either use a dedicated admin channel for each server or a "
                                            "central admin channel for all servers.\n"
                                            "If you want to use a central one, please provide the ID (optional)"),
                                          default=-1)
            channels = {}
            if audit_channel and audit_channel != -1:
                channels['audit'] = audit_channel
            if admin_channel and admin_channel != -1:
                channels['admin'] = admin_channel
            if channels:
                bot['channels'] = channels
            return main, bot

        def configure_no_discord() -> tuple[dict, dict]:
            print(_("\n2. [u]Bot Setup[/]"))
            guild_name = Prompt.ask(_("Please enter the name of your DCS group"))
            main = {
                "guild_id": random.randint(111111111111111111, 999999999999999999),
                "guild_name": guild_name,
                "autoupdate": autoupdate
            }
            print(_("You now need to setup your users.\nDCSServerBot uses the following internal roles:"))
            print({
                "Admin": _("Users can delete data and change the bot"),
                "DCS Admin": _("Users can restart missions, kick/ban users, etc.")
            })
            roles = {}
            for role in ["Admin", "DCS Admin"]:
                users = Prompt.ask(_("Which user(s) should get the [bold]{}[/] role?\n"
                                     "Please enter a comma-separated list of UCIDs").format(role)).split(',')
                roles[role] = [x.strip() for x in users]
            bot = {
                "no_discord": True,
                "roles": roles
            }
            return main, bot

        # initialize translations
        language = Prompt.ask("Which language do you speak?", choices=get_supported_languages(), default='EN')
        if language != 'EN':
            translations.set_language(f"{language.lower()}")
        _ = translations.get_translation("install")

        use_lang_in_game = False
        if language != 'EN':
            use_lang_in_game = Confirm.ask(
                _("The bot can be set to the same language, which means, that all Discord and in-game messages will be "
                  "in your language as well. Would you like me to configure the bot this way?"""), default=False)

        print(_("""
For a successful installation, you need to fulfill the following prerequisites:

    1. Installation of PostgreSQL from https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
    2. A Discord TOKEN for your bot from https://discord.com/developers/applications

"""))
        print(_("[i]You can skip the Discord TOKEN, if you decide to do a non-Discord-installation.[/]"))
        if not Confirm.ask(prompt=_("Have you fulfilled all these requirements?"), default=False):
            self.log.warning(_("Aborted: missing requirements"))
            exit(-2)

        print(_("\n1. [u]General Setup[/u]"))
        # check if we can enable autoupdate
        autoupdate = Confirm.ask(_("Do you want your DCSServerBot being auto-updated?"), default=True)
        use_discord = Confirm.ask(
            _("Do you want to run DCSServerBot with Discord support (recommended)?"), default=True)
        if use_discord:
            main, bot = configure_discord()
        else:
            main, bot = configure_no_discord()
        nodes = {}
        return main, nodes, bot

    def install(self, config_dir: str, user: str, database: str):
        global _

        major_version = int(platform.python_version_tuple()[1])
        if major_version <= 8:
            print(f"""
[red]!!! Your Python 3.{major_version} installation is not supported, you might face issues. Please use 3.9 or higher!!![/]
            """)
        print("""
[bright_blue]Hello! Thank you for choosing DCSServerBot.[/]
DCSServerBot supports everything from single server installations to huge server farms with multiple servers across 
the planet.

I will now guide you through the installation process.
If you need any further assistance, please visit the support discord, listed in the documentation.

        """)
        if not os.path.exists(os.path.join(config_dir, 'main.yaml')):
            main, nodes, bot = self.install_master()
            master = True
            servers = {}
            schedulers = {}
            i = 2
        else:
            main = yaml.load(Path(os.path.join(config_dir, 'main.yaml')).read_text(encoding='utf-8'))
            nodes = yaml.load(Path(os.path.join(config_dir, 'nodes.yaml')).read_text(encoding='utf-8'))
            bot = yaml.load(Path(os.path.join(config_dir, 'services', 'bot.yaml')).read_text(encoding='utf-8'))
            try:
                servers = yaml.load(Path(os.path.join(config_dir, 'servers.yaml')).read_text(encoding='utf-8'))
            except FileNotFoundError:
                servers = {}
            try:
                schedulers = yaml.load(
                    Path(os.path.join(config_dir, 'plugins', 'scheduler.yaml')).read_text(encoding='utf-8'))
            except FileNotFoundError:
                schedulers = {}

            _ = translations.get_translation("install")

            if self.node in nodes:
                if not Confirm.ask(_("[red]A configuration for this node exists already![/]\n"
                                     "Do you want to overwrite it?"), default=False):
                    self.log.warning(_("Aborted: configuration exists"))
                    exit(-1)
            else:
                print(_("[yellow]Configuration found, adding another node...[/]"))
            master = False
            i = 0

        print(_("\n{}. [u]Database Setup[/]").format(i + 1))
        if master:
            database_url = Install.get_database_url(user, database)
            if not database_url:
                self.log.error(_("Aborted: No valid Database URL provided."))
                exit(-1)
        else:
            if 'database' in main:
                database_url = main['database']['url']
            else:
                try:
                    database_url = next(node['database']['url'] for node in nodes.values() if node.get('database'))
                except StopIteration:
                    database_url = None
            if database_url:
                url = urlparse(database_url)
                hostname, port = self.get_database_host(url.hostname, url.port)
                database_url = f"{url.scheme}://{url.username}:{url.password}@{hostname}:{port}{url.path}?sslmode=prefer"
            else:
                database_url = Install.get_database_url(user, database)
                if not database_url:
                    self.log.error(_("Aborted: No valid Database URL provided."))
                    exit(-1)

        print(_("\n{}. [u]Node Setup[/]").format(i+2))
        if sys.platform == 'win32':
            dcs_installation = Install.get_dcs_installation_win32() or '<see documentation>'
        else:
            dcs_installation = Install.get_dcs_installation_linux()
        node = nodes[self.node] = {
            "listen_port": max([
                n.get('listen_port', 10041 + idx) for idx, n in enumerate(nodes.values())
            ]) + 1 if nodes else 10042,
            "use_upnp": self.use_upnp
        }
        public_ip = asyncio.run(utils.get_public_ip())
        if Confirm.ask(_("Is {} a static IP-address for this node?").format(public_ip), default=False):
            node['public_ip'] = public_ip
        if 'database' not in main:
            node["database"] = {
                "url": database_url
            }
        if dcs_installation:
            node["DCS"] = {
                "installation": dcs_installation
            }

            if Confirm.ask(_("Do you want your DCS installation being auto-updated by the bot?"), default=True):
                node["DCS"]["autoupdate"] = True

            # Check for SRS
            srs_path = os.path.expandvars('%ProgramFiles%\\DCS-SimpleRadio-Standalone')
            if not os.path.exists(srs_path):
                srs_path = Prompt.ask(_("Please enter the path to your DCS-SRS installation.\n"
                                        "Press ENTER, if there is none."))
            if srs_path:
                self.log.info(_("DCS-SRS installation path: {}").format(srs_path))
                node['extensions'] = {
                    'SRS': {
                        'installation': srs_path
                    }
                }
            else:
                self.log.info(_("DCS-SRS not configured."))

            print(_("\n{}. [u]DCS Server Setup[/]").format(i+3))
            scheduler = schedulers[self.node] = {}
            node['instances'] = {}
            # calculate unique bot ports
            bot_port = max([
                i.get('bot_port', 6665 + idx)
                for idx, i in enumerate([
                    n['instances'] for n in nodes.values() if 'instances' in n
                ])
            ]) + 1 if nodes else 6666

            # calculate unique SRS ports
            srs_port = max([
                i.get('extensions', {}).get('SRS', {}).get('port', 5001 + idx)
                for idx, i in enumerate([
                    n['instances'] for n in nodes.values() if 'instances' in n
                ])
            ]) + 1 if nodes else 5002

            print(_("Searching for existing DCS server configurations ..."))
            instances = utils.findDCSInstances()
            if not instances:
                print(_("No configured DCS servers found."))
            for name, instance in instances:
                if not name or name in ['n/a', 'DCS Server']:
                    print(_("DCS Server without name found in Saved Games\\{}.").format(instance))
                    if not Confirm.ask(_("Would you like to give it a name?"), default=True):
                        continue
                    name = Prompt.ask("Please enter a server name:")
                else:
                    print(_('\n[i]DCS Server "{}" found.[/i]\n').format(name))

                if Confirm.ask(_('Would you like to manage this server through DCSServerBot?'), default=True):
                    self.log.info(_("Adding instance {instance} with server {name} ...").format(instance=instance,
                                                                                                name=name))
                    node['instances'][instance] = {
                        "bot_port": bot_port,
                        "home": os.path.join(SAVED_GAMES, instance)
                    }
                    if srs_path:
                        node['instances'][instance]['extensions'] = {
                            "SRS": {
                                "config": "{instance.home}/Config/SRS.cfg",
                                "port": srs_port
                            }
                        }
                        srs_config = os.path.join(SAVED_GAMES, instance, 'Config', 'SRS.cfg')
                        if not os.path.exists(os.path.expandvars(srs_config)):
                            if os.path.exists(os.path.join(srs_path, "server.cfg")):
                                shutil.copy2(os.path.join(srs_path, "server.cfg"), os.path.expandvars(srs_config))
                            else:
                                print(_("[red]SRS configuration could not be created.\n"
                                        "Please copy your server.cfg to {} manually.[/]").format(srs_config))
                                self.log.warning(_("SRS configuration could not be created, manual setup necessary."))
                    bot_port += 1
                    srs_port += 2

                    # we only set up channels if we configure a discord bot
                    if not bot.get('no_discord', False):
                        channels = {
                            "Status Channel": _("To display the mission and player status."),
                            "Chat Channel": _("Optional: An in-game chat replication.")
                        }
                        if not bot.get('channels', {}).get('admin'):
                            channels['Admin Channel'] = _("For admin commands.")
                        print(_("DCSServerBot uses up to {} channels per supported server:").format(len(channels)))
                        print(channels)
                        print(_("\nThe Status Channel should be readable by everyone and only writable by the bot.\n"
                                "The Chat Channel should be readable and writable by everyone.\n"
                                "The Admin channel - central or not - should only be readable and writable by Admin and DCS Admin users.\n\n"
                                "You can create these channels now, as I will ask for the IDs in a bit.\n"
                                "DCSServerBot needs the following permissions on them to work:\n\n"
                                "    - View Channel\n"
                                "    - Send Messages\n"
                                "    - Read Messages\n"
                                "    - Read Message History\n"
                                "    - Add Reactions\n"
                                "    - Attach Files\n"
                                "    - Embed Links\n"
                                "    - Manage Messages\n\n"))

                        servers[name] = {
                            "channels": {
                                "status": IntPrompt.ask(_("Please enter the ID of your [bold]Status Channel[/]")),
                                "chat": IntPrompt.ask(_("Please enter the ID of your [bold]Chat Channel[/] (optional)"),
                                                      default=-1)
                            }
                        }
                        if not bot.get('channels', {}).get('admin'):
                            servers[name]['channels']['admin'] = IntPrompt.ask(
                                _("Please enter the ID of your [bold]Admin Channel[/]"))
                    else:
                        servers[name] = {}
                    if Prompt.ask(_("Do you want DCSServerBot to autostart this server?"), choices=['y', 'n'],
                                  default='y') == 'y':
                        scheduler[instance] = {
                            "schedule": {
                                "00-24": "YYYYYYY"
                            }
                        }
                    else:
                        scheduler[instance] = {}
                    self.log.info(_("Instance {} configured.").format(instance))

        print(_("\n\nAll set. Writing / updating your config files now..."))
        if master:
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, 'main.yaml'), mode='w', encoding='utf-8') as out:
                yaml.dump(main, out)
            print(_("- Created {}").format(os.path.join(config_dir, "main.yaml")))
            self.log.info(_("{} written.").format(os.path.join(config_dir, "main.yaml")))
            os.makedirs(os.path.join(config_dir, 'services'), exist_ok=True)
            with open(os.path.join(config_dir, 'services', 'bot.yaml'), mode='w', encoding='utf-8') as out:
                yaml.dump(bot, out)
            print(_("- Created {}").format(os.path.join(config_dir, 'services', 'bot.yaml')))
            self.log.info(_("{} written.").format(os.path.join(config_dir, 'services', 'bot.yaml')))
        with open(os.path.join(config_dir, 'nodes.yaml'), mode='w', encoding='utf-8') as out:
            yaml.dump(nodes, out)
        print(_("- Created {}").format(os.path.join(config_dir, "nodes.yaml")))
        self.log.info(_("{} written.").format(os.path.join(config_dir, "nodes.yaml")))
        with open(os.path.join(config_dir, 'servers.yaml'), mode='w', encoding='utf-8') as out:
            yaml.dump(servers, out)
        print(_("- Created {}").format(os.path.join(config_dir, "servers.yaml")))
        self.log.info(_("{} written.").format(os.path.join(config_dir, "servers.yaml")))
        # write plugin configuration
        if schedulers:
            os.makedirs(os.path.join(config_dir, 'plugins'), exist_ok=True)
            with open(os.path.join(config_dir, 'plugins', 'scheduler.yaml'), mode='w', encoding='utf-8') as out:
                yaml.dump(schedulers, out)
            print(_("- Created {}").format(os.path.join(config_dir, 'plugins', 'scheduler.yaml')))
            self.log.info(_("{} written.").format(os.path.join(config_dir, 'plugins', 'scheduler.yaml')))
        if dcs_installation:
            try:
                os.chmod(os.path.join(dcs_installation, 'Scripts', 'MissionScripting.lua'), stat.S_IWUSR)
            except PermissionError:
                print(_("[red]You need to give DCSServerBot write permissions on {} to desanitize your MissionScripting.lua![/]").format(dcs_installation))
        if sys.platform == 'win32':
            run_script = 'run.cmd'
        else:
            run_script = 'run.sh'
        print(_(f"\n[green]Your basic DCSServerBot configuration is finished.[/]\n\n"
                f"You can now review the created configuration files below your config folder of your DCSServerBot-installation.\n"
                f"There is much more to explore and to configure, so please don't forget to have a look at the documentation!\n\n"
                f"You can start DCSServerBot with:\n\n"
                f"    [bright_black]{run_script}[/]\n\n"))
        self.log.info(_("Installation finished."))


if __name__ == "__main__":
    # get the command line args from the core
    args = COMMAND_LINE_ARGS
    console = Console()
    try:
        Install(node=args.node).install(config_dir=args.config, user=args.user, database=args.database)
    except KeyboardInterrupt:
        pass
    except Exception:
        console.print_exception(show_locals=True, max_frames=1)
        print(_("\nAborted."))
