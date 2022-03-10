import winreg
from core import utils
from os import path


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


if __name__ == "__main__":
    if not path.exists('config/dcsserverbot.ini'):
        Install.install()
    else:
        print('DCSServerBot seems to be installed already.\nRun "run.py" instead.')
