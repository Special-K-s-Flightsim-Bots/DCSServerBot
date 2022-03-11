import os
import psycopg2
import socket
import winreg
from configparser import ConfigParser
from core import utils
from os import path
from typing import Optional


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
    def install():
        print('Welcome to the DCSSeverBot!\n\nI will create a file named config/dcsserverbot.ini for you now...')
        dcs_installation = None
        key = skey = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics", 0)
            num_dcs_installs = winreg.QueryInfoKey(key)[0]
            if num_dcs_installs == 0:
                print('Attention: No installation of DCS World found on this PC. Autostart will not work.')
            elif num_dcs_installs > 1:
                print('I\'ve found multiple installations of DCS World on this PC:')
                for i in range(0, num_dcs_installs):
                    print(f'{i+1}: {winreg.EnumKey(key, i)}')
                num = int(input('\nPlease specify, which installation you want the bot to use: '))
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{winreg.EnumKey(key, num-1)}", 0)
            else:
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{winreg.EnumKey(key, 0)}", 0)
            if skey:
                dcs_installation = winreg.QueryValueEx(skey, 'Path')[0]
                if not path.exists(dcs_installation):
                    dcs_installation = None
                    raise OSError
        except FileNotFoundError:
            print('Attention: No installation of DCS World found on this PC. Autostart will not work.')
        except OSError:
            print('Attention: Your DCS was not installed correctly. Autostart will not work.')
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()

        with open('config/dcsserverbot.ini', 'w') as inifile:
            inifile.writelines([
                '; This file is generated and has to be amended to your needs!\n',
                '[BOT]\n',
                'OWNER=<see documentation>\n',
                'TOKEN=<see documentation>\n',
                'DATABASE_URL=postgres://<user>:<pass>@localhost:5432/<database>\n',
                '\n'])
            if dcs_installation:
                inifile.writelines([
                    '[DCS]\n',
                    'DCS_INSTALLATION={}\n'.format(dcs_installation.replace('\\', '\\\\')),
                    '\n'
                ])
            dcs_port = 6666
            for installation in utils.findDCSInstallations():
                inifile.writelines([
                    f'[{installation}]\n',
                    'DCS_HOST=127.0.0.1\n',
                    f'DCS_PORT={dcs_port}\n',
                    r'DCS_HOME = %%USERPROFILE%%\\Saved Games\\' + f'{installation}\n',
                    'AUTOSTART_DCS=false\n',
                    'ADMIN_CHANNEL=<see documentation>\n',
                    'STATUS_CHANNEL=<see documentation>\n',
                    'CHAT_CHANNEL=<see documentation>\n',
                    '\n'
                ])
                dcs_port += 1
        print('Please check config/dcsserverbot.ini and edit it according to the installation documentation before '
              'you restart the bot.')

    @staticmethod
    def verify():
        def check_database(url: str) -> bool:
            try:
                conn = psycopg2.connect(url)
                conn.close()
                return True
            except psycopg2.Error:
                return False

        def check_channel(channel: str) -> bool:
            return channel.strip('-').isnumeric()

        config = ConfigParser()
        if path.exists('config/default.ini'):
            config.read('config/default.ini')
        else:
            raise Exception('Your installation is broken.')
        if path.exists('config/dcsserverbot.ini'):
            config.read('config/dcsserverbot.ini')
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
                    raise InvalidParameter('BOT', 'AUTOUPATE', 'Make sure git for Windows is installed and the bot '
                                                               'has been installed by using git clone.')
            if 'AUDIT_CHANNEL' in config['BOT'] and not check_channel(config['BOT']['AUDIT_CHANNEL']):
                raise InvalidParameter('BOT', 'AUDIT_CHANNEL', 'Invalid channel.')
        except KeyError as key:
            raise MissingParameter('BOT', str(key))
        # check DCS section
        try:
            if not path.exists(os.path.expandvars(config['DCS']['DCS_INSTALLATION'])):
                raise InvalidParameter('DCS', 'DCS_INSTALLATION', 'Path does not exist.')
        except KeyError as key:
            raise MissingParameter('DCS', str(key))
        num_installs = 0
        ports = set()
        for installation in utils.findDCSInstallations():
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
                        raise InvalidParameter(installation, 'DCS_PORT', 'Port has to be unique for all servers!')
                    ports.add(config[installation]['DCS_PORT'])
                if not path.exists(os.path.expandvars(config[installation]['DCS_HOME'])):
                    raise InvalidParameter(installation, 'DCS_HOME', 'Path does not exist.')
                if 'SRS_CONFIG' in config[installation]:
                    if not path.exists(os.path.expandvars(config[installation]['SRS_CONFIG'])):
                        raise InvalidParameter(installation, 'SRS_CONFIG', 'Path does not exist.')
                    try:
                        socket.inet_aton(config[installation]['SRS_HOST'])
                    except socket.error:
                        raise InvalidParameter(installation, 'SRS_HOST', 'Invalid IPv4 address.')
                    if not config[installation]['SRS_PORT'].isnumeric():
                        raise InvalidParameter(installation, 'SRS_PORT', 'Please enter a number from 1024 to 65535 ('
                                                                         'default: 5002).')
                for channel in ['CHAT_CHANNEL', 'ADMIN_CHANNEL', 'STATUS_CHANNEL']:
                    if not check_channel(config[installation][channel]):
                        raise InvalidParameter(installation, channel, 'Invalid channel.')
            except KeyError as key:
                raise MissingParameter(installation, str(key))
        if num_installs == 0:
            raise Exception('Your dcsserverbot.ini does not contain any matching server configuration.')


if __name__ == "__main__":
    if not path.exists('config/dcsserverbot.ini'):
        Install.install()
    else:
        print('DCSServerBot seems to be installed already.\nRun "run.py" instead.')
