# bot.py
import asyncio
import configparser
import discord
import logging
import os
import shutil
import sqlite3
from sqlite3 import Error
from discord.ext import commands
from logging.handlers import RotatingFileHandler
from os import path

config = configparser.ConfigParser()
config.read('config/dcsserverbot.ini')

# COGs to load
COGS = ['cogs.dcs', 'cogs.statistics', 'cogs.help']

# Database Configuration
DATABASE = 'dcsserverbot.db'
TABLES_SQL = 'sql/tables.sql'


def get_prefix(client, message):
    prefixes = [config['BOT']['COMMAND_PREFIX']]
    # Allow users to @mention the bot instead of using a prefix
    return commands.when_mentioned_or(*prefixes)(client, message)


# Create the Bot
bot = commands.Bot(command_prefix=get_prefix,
                   description='Interact with DCS World servers',
                   owner_id=int(config['BOT']['OWNER']),
                   case_insensitive=True,
                   intents=discord.Intents.all())

# Allow COGs to access configuration
bot.config = config

# Initialize the logger and i18n
bot.log = logging.getLogger(name='dcsserverbot')
bot.log.setLevel(logging.DEBUG)
fh = RotatingFileHandler('dcsserverbot.log', maxBytes=10*1024*2024, backupCount=2)
fh.setLevel(logging.INFO)
fh.doRollover()
ch = logging.StreamHandler()
ch.setLevel(logging.ERROR)
bot.log.addHandler(fh)
bot.log.addHandler(ch)

# Initialize DCS servers
bot.DCSServers = []


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} - {bot.user.id}')
    bot.remove_command('help')
    for cog in COGS:
        bot.load_extension(cog)
    return


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send('This command can\'t be used in a DM.')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('Parameter missing. Try !help')
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.errors.CheckFailure):
        await ctx.send('You don\'t have the rights to use that command.')
    elif isinstance(error, asyncio.TimeoutError):
        await ctx.send('You\'ve waited too long. Aborted.')
    else:
        await ctx.send(str(error))


@bot.event
async def on_message(message):
    if (next((item for item in bot.DCSServers if int(item["chat_channel"]) == message.channel.id), None) is not None):
        if (message.content.startswith(config['BOT']['COMMAND_PREFIX']) is False):
            message.content = config['BOT']['COMMAND_PREFIX'] + 'chat ' + message.content
    await bot.process_commands(message)


@bot.command(description='Reloads a COG', usage='[cog]')
@commands.is_owner()
async def reload(ctx, cog=None):
    for c in COGS:
        if ((cog is None) or (c == cog)):
            bot.reload_extension(c)
    if (cog is None):
        await ctx.send('All COGs reloaded.')
    else:
        await ctx.send('COG {} reloaded.'.format(cog))

# Initialize the database
if (path.exists(TABLES_SQL)):
    bot.log.info('Initializing Database ...')
    try:
        bot.conn = sqlite3.connect(DATABASE)
        bot.conn.row_factory = sqlite3.Row
        cursor = bot.conn.cursor()
        with open(TABLES_SQL) as tables_sql:
            for query in tables_sql.readlines():
                bot.log.debug(query.rstrip())
                cursor.execute(query.rstrip())
        bot.conn.commit()
        bot.log.info('Database initialized.')
    except Error as e:
        bot.log.exception(e)

bot.log.info('Installing Hook ...')
dcs_path = os.path.expandvars(config['DCS']['DCS_HOME'] + '\\Scripts')
assert path.exists(dcs_path), 'Can\'t find DCS installation directory. Exiting.'
shutil.copytree('./Scripts', dcs_path, dirs_exist_ok=True)
bot.log.info('Hook installed.')
# TODO change sanitizeModules

bot.run(config['BOT']['TOKEN'], bot=True, reconnect=True)
