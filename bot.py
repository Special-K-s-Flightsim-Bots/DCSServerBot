# bot.py
import asyncio
import configparser
import discord
import logging
import os
import shutil
import platform
import psycopg2
import psycopg2.extras
import sqlite3
from contextlib import closing
from discord.ext import commands
from logging.handlers import RotatingFileHandler
from os import path
from psycopg2 import pool
from sqlite3 import Error

config = configparser.ConfigParser()
config.read('config/dcsserverbot.ini')

# COGs to load
COGS = ['cogs.master', 'cogs.statistics', 'cogs.help'] if config.getboolean('BOT', 'MASTER') is True else ['cogs.agent']

# Database Configuration
SQLITE_DATABASE = 'dcsserverbot.db'
TABLES_SQL = 'sql/tables.sql'
POOL_MIN = 5 if config.getboolean('BOT', 'MASTER') is True else 2
POOL_MAX = 10 if config.getboolean('BOT', 'MASTER') is True else 5


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
ch.setLevel(logging.WARN)
bot.log.addHandler(fh)
bot.log.addHandler(ch)

# Initialize DCS servers
bot.DCSServers = []


@bot.event
async def on_ready():
    bot.log.warning(f'Logged in as {bot.user.name} - {bot.user.id}')
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
        await ctx.send('A timeout occured. Is the DCS server running?')
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
    bot.config.read('config/dcsserverbot.ini')
    for c in COGS:
        if ((cog is None) or (c == cog)):
            bot.reload_extension(c)
    if (cog is None):
        await ctx.send('All COGs reloaded.')
    else:
        await ctx.send('COG {} reloaded.'.format(cog))

# Creating connection pool
bot.pool = pool.ThreadedConnectionPool(POOL_MIN, POOL_MAX, config['BOT']['DATABASE_URL'], sslmode='require')
if (config.getboolean('BOT', 'MASTER') is True):
    # Initialize the database
    if (path.exists(TABLES_SQL)):
        bot.log.warning('Initializing Database ...')
        conn = bot.pool.getconn()
        try:
            cursor = conn.cursor()
            with open(TABLES_SQL) as tables_sql:
                for query in tables_sql.readlines():
                    bot.log.debug(query.rstrip())
                    cursor.execute(query.rstrip())
            conn.commit()
            bot.log.warning('Database initialized.')
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            bot.log.exception(error)
            exit(-1)
        bot.pool.putconn(conn)

    if (path.exists(SQLITE_DATABASE)):
        bot.log.warning('SQLite Database found. Migrating... (this may take a while)')
        conn_tgt = bot.pool.getconn()
        try:
            with closing(sqlite3.connect(SQLITE_DATABASE)) as conn_src:
                conn_src.row_factory = sqlite3.Row
                with closing(conn_src.cursor()) as cursor_src:
                    for table in [row[0] for row in cursor_src.execute('SELECT name FROM sqlite_master WHERE type=\'table\' and name not like \'sqlite_%\'').fetchall()]:
                        bot.log.info('Migrating table ' + table + ' ...')
                        for row in [dict(row) for row in cursor_src.execute('SELECT * FROM ' + table).fetchall()]:
                            if ('ban' in row):
                                row['ban'] = 'f' if (row['ban'] == 0) else 't'
                            # add a new column agent_host to support multiple bot hosts
                            if (table == 'servers'):
                                row['agent_host'] = platform.node()
                            with closing(conn_tgt.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor_tgt:
                                keys = row.keys()
                                columns = ','.join(keys)
                                values = ','.join(['%({})s'.format(k) for k in keys])
                                SQL = 'INSERT INTO ' + table + '({0}) VALUES ({1})'.format(columns, values)
                                cursor_tgt.execute(SQL, row)
            bot.log.info('Re-initializing sequences...')
            with closing(conn_tgt.cursor()) as cursor_tgt:
                cursor_tgt.execute('SELECT setval(\'missions_id_seq\', (select max(id)+1 from missions), false)')
            conn_tgt.commit()
        except (Error, Exception, psycopg2.DatabaseError) as error:
            conn_tgt.rollback()
            bot.log.exception(error)
            exit(-1)
        bot.pool.putconn(conn_tgt)
        new_filename = SQLITE_DATABASE[0: SQLITE_DATABASE.rfind('.')] + '.bak'
        bot.log.warning('SQLite Database migrated. Renaming to ' + new_filename)
        os.rename(SQLITE_DATABASE, new_filename)


# Installing Hook
dcs_path = os.path.expandvars(config['DCS']['DCS_HOME'] + '\\Scripts')
assert path.exists(dcs_path), 'Can\'t find DCS installation directory. Exiting.'
ignore = None
if (path.exists(dcs_path + '\\net\\DCSServerBot')):
    bot.log.info('Updating Hook ...')
    ignore = shutil.ignore_patterns('DCSServerBotConfig.lua')
else:
    bot.log.info('Installing Hook ...')
shutil.copytree('./Scripts', dcs_path, dirs_exist_ok=True, ignore=ignore)
bot.log.info('Hook installed.')

# TODO change sanitizeModules
bot.log.warning('Starting {} at {}'.format('Master' if config.getboolean(
    'BOT', 'MASTER') is True else 'Agent', platform.node()))
bot.run(config['BOT']['TOKEN'], bot=True, reconnect=True)
