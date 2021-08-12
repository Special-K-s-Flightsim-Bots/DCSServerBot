import asyncio
import discord
import json
import psycopg2
import psycopg2.extras
from os import path

BOT_CONFIG = 'config/dcsserverbot.ini.json'
DCS_CONFIG = 'Scripts/net/DCSServerBot/DCSServerBotConfig.lua.json'
BOT_INSTALL = 'https://discord.com/api/oauth2/authorize?client_id={}&permissions=256064&scope=bot'


def install():

    class Client(discord.Client):

        async def on_ready(self):
            print('on_ready()')

    def check_token(token):
        try:
            client = Client()
            loop = asyncio.get_event_loop()
            loop.create_task(client.start(token))
#            Thread(target=loop.run_forever).start()

            return True
        except discord.errors.LoginFailure:
            print('This token is invalid. Please enter a valid token!')
            return False

    def check_database(url):
        conn = None
        try:
            conn = psycopg2.connect(url=url)
            return True
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
            return False
        finally:
            if conn is not None:
                conn.close()

    print('Welcome to DCSServerBot!\nI will lead you through the configuration process.\n')
    with open(BOT_CONFIG, 'r') as bot_config:
        conf = json.load(bot_config)
        if (path.exists(conf['filename'])):
            yn = input(
                'There is an existing config file ({}) available. Do you want to overwrite it (Y/N)?'.format(conf['filename']))
            if (yn.upper() != 'Y'):
                print('Aborting.')
                return
            with open(conf['filename'], 'w') as bot_config:
                for s in conf['sections']:
                    print('Section {}'.format(s['name']))
                    for v in s['values']:
                        while (True):
                            if ('default' in v):
                                value = input('{} [default: {}]: '.format(v['description'], v['default']))
                                if (len(value) == 0):
                                    value = v['default']
                            elif ('example' in v):
                                value = input('{} (example: {}): '.format(v['description'], v['example']))
                            else:
                                value = input('{}: '.format(v['description']))
                            if ('check' in v):
                                if (locals()[v['check']](value) is False):
                                    continue
                            bot_config.write('{} = {}\n'.format(v['name'], value))
                            break


if __name__ == "__main__":
    install()
