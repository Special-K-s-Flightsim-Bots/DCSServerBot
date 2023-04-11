import os
import psycopg
import secrets
import socket
import winreg
from configparser import ConfigParser
from contextlib import closing, suppress
from getpass import getpass
from os import path
from core import utils
from typing import Optional, Tuple


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
    def get_dcs_installation() -> Optional[str]:
        dcs_installation = None
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
                print(f"{installs[0][0]} found.")
                return installs[0][1]
            else:
                print('I\'ve found multiple installations of DCS World on this PC:')
                for i in range(0, len(installs)):
                    print(f'{i+1}: {installs[i][0]}')
                num = int(input('\nPlease specify, which installation you want the bot to use: '))
                return installs[num-1][1]
        except (FileNotFoundError, OSError):
            while dcs_installation is None:
                dcs_installation = input("Please enter the path to your DCS World installation: ")
                if not os.path.exists(dcs_installation):
                    print("Directory not found. Please try again.")
                    dcs_installation = None
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()
        return dcs_installation

    @staticmethod
    def get_database_url() -> Optional[str]:
        port = 5432
        if not utils.is_open('127.0.0.1', port):
            print('No PostgreSQL-database found on port 5432.')
            port = input('Enter the port to your PostgreSQL-database or press ENTER if you need to install it first: ')
            if not port.isnumeric():
                print('Aborting.')
                exit(-1)
        while True:
            passwd = getpass('Please enter your PostgreSQL master password (user=postgres): ')
            url = f'postgres://postgres:{passwd}@localhost:{port}/postgres'
            with psycopg.connect(url, autocommit=True) as conn:
                with closing(conn.cursor()) as cursor:
                    passwd = secrets.token_urlsafe(8)
                    try:
                        cursor.execute("CREATE USER dcsserverbot WITH ENCRYPTED PASSWORD %s", (passwd, ))
                    except psycopg.Error:
                        print('Existing dcsserverbot database found.')
                        while True:
                            passwd = getpass("Please enter your password for user 'dcsserverbot': ")
                            try:
                                with psycopg.connect(
                                        f"postgres://dcsserverbot:{passwd}@localhost:{port}/dcsserverbot"):
                                    pass
                                break
                            except psycopg.Error:
                                print("Wrong password. Try again.")
                    with suppress(psycopg.Error):
                        cursor.execute("CREATE DATABASE dcsserverbot")
                        cursor.execute("GRANT ALL PRIVILEGES ON DATABASE dcsserverbot TO dcsserverbot")
                        cursor.execute("ALTER DATABASE dcsserverbot OWNER TO dcsserverbot")
                    print("Database user and database created.")
                    return f"postgres://dcsserverbot:{passwd}@localhost:{port}/dcsserverbot"

    @staticmethod
    def install():
        print("Welcome to the DCSSeverBot!\n\n")
        print("Let's create a first version of your dcsserverbot.ini now!")
        dcs_installation = Install.get_dcs_installation() or '<see documentation>'
        token = input('Please enter your discord TOKEN (see documentation): ') or '<see documentation>'
        database_url = Install.get_database_url()
        with open('config/dcsserverbot.ini', 'w') as inifile:
            inifile.writelines([
                '; This file is generated and has to be amended to your needs!\n',
                '[BOT]\n',
                'OWNER=<see documentation>\n',
                f'TOKEN={token}\n',
                f'DATABASE_URL={database_url}\n'
            ])
            try:
                import git
                inifile.write('AUTOUPDATE=true\n')
            except ImportError:
                pass
            if dcs_installation:
                inifile.writelines([
                    '\n',
                    '[DCS]\n',
                    'DCS_INSTALLATION={}\n'.format(dcs_installation.replace('\\', '\\\\'))
                ])
            print("Searching DCS servers ...")
            dcs_port = 6666
            for name, installation in utils.findDCSInstallations():
                if input(f'Do you want to add server "{name}" (Y/N)?').upper() == 'Y':
                    inifile.writelines([
                        '\n',
                        f'[{installation}]\n',
                        'DCS_HOST=127.0.0.1\n',
                        f'DCS_PORT={dcs_port}\n',
                        r'DCS_HOME = %%USERPROFILE%%\\Saved Games\\' + f'{installation}\n',
                        'ADMIN_CHANNEL=<see documentation>\n',
                        'STATUS_CHANNEL=<see documentation>\n',
                        'CHAT_CHANNEL=<see documentation>\n'
                    ])
                    dcs_port += 1
        print("\nI've created a DCSServerBot configuration file \"config/dcsserverbot.ini\" for you.\n"
              "Please review it, before you launch DCSServerBot.")

    @staticmethod
    def verify():
        def check_database(url: str) -> bool:
            try:
                with psycopg.connect(url):
                    return True
            except psycopg.Error:
                return False

        def check_channel(channel: str) -> bool:
            return channel.strip('-').isnumeric()

        config = ConfigParser()
        if path.exists('config/default.ini'):
            config.read('config/default.ini', encoding='utf-8')
        else:
            raise Exception("Your installation is broken, default.ini is missing!")
        if path.exists('config/dcsserverbot.ini'):
            config.read('config/dcsserverbot.ini', encoding='utf-8')
        else:
            # should never happen as the file is being auto-generated
            raise Exception('dcsserverbot.ini is not there. Please create such a file according to the documentation.')
        # check BOT section
        try:
            if not config['BOT']['OWNER'].isnumeric():
                raise InvalidParameter('BOT', 'OWNER', 'Value has to be numeric.')
            if not check_database(config['BOT']['DATABASE_URL']):
                raise InvalidParameter('BOT', 'DATABASE_URL', 'Can\'t connect to database.')
            if config['BOT']['HOST'] not in ['0.0.0.0', '127.0.0.1', socket.gethostbyname(socket.gethostname())]:
                raise InvalidParameter('BOT', 'HOST', "Invalid IPv4 address.")
            if not config['BOT']['PORT'].isnumeric():
                raise InvalidParameter('BOT', 'PORT', 'Please enter a number from 1024 to 65535.')
            if 'AUTOUPDATE' in config['BOT'] and config.getboolean('BOT', 'AUTOUPDATE'):
                try:
                    import git
                except ImportError:
                    raise InvalidParameter('BOT', 'AUTOUPDATE', 'Make sure git for Windows is installed and the bot '
                                                                'has been installed by using "git clone".')
            if 'AUDIT_CHANNEL' in config['BOT'] and not check_channel(config['BOT']['AUDIT_CHANNEL']):
                raise InvalidParameter('BOT', 'AUDIT_CHANNEL', 'Invalid channel.')
            if 'PLUGINS' in config['BOT'] and \
                    config['BOT']['PLUGINS'] != 'dashboard, mission, scheduler, help, admin, userstats, ' \
                                                'missionstats, creditsystem, gamemaster, cloud':
                print("Please don't change the PLUGINS parameter, use OPT_PLUGINS instead!")
        except KeyError as key:
            raise MissingParameter('BOT', str(key))
        # check DCS section
        try:
            if not path.exists(os.path.expandvars(config['DCS']['DCS_INSTALLATION'])):
                raise InvalidParameter('DCS', 'DCS_INSTALLATION', 'Path does not exist.')
        except KeyError as key:
            raise MissingParameter('DCS', str(key))
        num_installs = 0
        ports = set(config['BOT']['PORT'])
        for _, installation in utils.findDCSInstallations():
            try:
                if installation not in config:
                    continue
                num_installs += 1
                try:
                    socket.inet_aton(config[installation]['DCS_HOST'])
                except socket.error:
                    raise InvalidParameter(installation, 'DCS_HOST', 'Invalid IPv4 address.')
                if not config[installation]['DCS_PORT'].isnumeric():
                    raise InvalidParameter(installation, 'DCS_PORT', 'Please enter a number from 1024 to 65535 ('
                                                                     'default: 6666).')
                else:
                    if config[installation]['DCS_PORT'] in ports:
                        raise InvalidParameter(installation, 'DCS_PORT', 'Ports have to be unique for all servers!')
                    elif config[installation]['DCS_PORT'] in [8088, 10308, 10309]:
                        raise InvalidParameter(installation, 'DCS_PORT',
                                               "Don't use the port of your DCS server (""10308), webgui_port (8088) or "
                                               "webrtc_port (""10309)!")
                    ports.add(config[installation]['DCS_PORT'])
                if not path.exists(os.path.expandvars(config[installation]['DCS_HOME'])):
                    # ignore missing directories in the DCS section, as people might have a serverSettings.lua in their
                    # DCS folder but no server configured
                    if installation == 'DCS':
                        continue
                    raise InvalidParameter(installation, 'DCS_HOME', 'Path does not exist.')
                for channel in ['CHAT_CHANNEL', 'ADMIN_CHANNEL', 'STATUS_CHANNEL']:
                    if not check_channel(config[installation][channel]):
                        raise InvalidParameter(installation, channel, 'Invalid channel.')
            except KeyError as key:
                raise MissingParameter(installation, str(key))
        if num_installs == 0:
            raise Exception('Your dcsserverbot.ini does not contain any server configuration.')


if __name__ == "__main__":
    if not path.exists('config/dcsserverbot.ini'):
        Install.install()
    else:
        print('DCSServerBot seems to be installed already.\nRun "run.py" instead.')
