import aiohttp
import asyncio
import discord
import json
import os
import platform
import psycopg2
import psycopg2.extras
import re
import shlex
import string
import subprocess
from contextlib import closing, suppress
from core import utils, DCSServerBot, Plugin, Report, const
from core.const import Status
from discord.ext import commands, tasks
from typing import Union, List, Optional
from zipfile import ZipFile
from .listener import AdminEventListener


class Agent(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_pending = False
        self.update_bot_status.start()
        if self.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.start()

    def cog_unload(self):
        if self.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.cancel()
        self.update_bot_status.cancel()
        super().cog_unload()

    @commands.command(description='Lists the registered DCS servers')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def servers(self, ctx):
        if len(self.globals) > 0:
            for server_name, server in self.globals.items():
                if server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                    players = self.bot.player_data[server['server_name']]
                    num_players = len(players[players['active'] == True]) + 1
                    report = Report(self.bot, 'mission', 'serverStatus.json')
                    env = await report.render(server=server, num_players=num_players)
                    await ctx.send(embed=env.embed)
        else:
            await ctx.send('No server running on host {}'.format(platform.node()))

    async def do_update(self, warn_times: List[int], ctx=None):
        self.update_pending = True
        if ctx:
            await ctx.send('Shutting down DCS servers, warning users before ...')
        else:
            self.log.info('Shutting down DCS servers, warning users before ...')
        servers = []
        for server_name, server in self.globals.items():
            if 'maintenance' in server:
                servers.append(server)
            else:
                server['maintenance'] = True
            if server['status'] in [Status.RUNNING, Status.PAUSED]:
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            self.bot.sendtoDCS(server, {
                                'command': 'sendPopupMessage',
                                'message': f'Server is going down for a DCS update in {utils.format_time(warn_time)}!',
                                'to': 'all',
                                'time': self.config['BOT']['MESSAGE_TIMEOUT']
                             })
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await utils.shutdown_dcs(self, server)
        if ctx:
            await ctx.send('Updating DCS World. Please wait, this might take some time ...')
        else:
            self.log.info('Updating DCS World ...')
        subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
            self.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs_updater.exe')
        utils.sanitize(self)
        if ctx:
            await ctx.send('DCS World updated to the latest version.\nStarting up DCS servers again ...')
        else:
            self.log.info('DCS World updated to the latest version.\nStarting up DCS servers again ...')
        for server_name, server in self.globals.items():
            if server not in servers:
                # let the scheduler do its job
                del server['maintenance']
            else:
                # the server was running before (being in maintenance mode), so start it again
                utils.startup_dcs(self, server)
        self.update_pending = False

    @commands.command(description='Update a DCS Installation')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def update(self, ctx):
        if self.update_pending:
            await ctx.send('An update is already running, please wait ...')
            return
        # check versions
        branch, old_version = utils.getInstalledVersion(self.config['DCS']['DCS_INSTALLATION'])
        new_version = await utils.getLatestVersion(branch)
        if old_version == new_version:
            await ctx.send('Your installed version {} is the latest on branch {}.'.format(old_version, branch))
        elif new_version:
            if await utils.yn_question(self, ctx, 'Would you like to update from version {} to {}?\nAll running '
                                                  'DCS servers will be shut down!'.format(old_version,
                                                                                          new_version)) is True:
                await self.bot.audit(f"started an update of all DCS servers on node {platform.node()}.",
                                     user=ctx.message.author)
                await self.do_update([120, 60], ctx)
        else:
            await ctx.send("Can't check the latest version on the DCS World website. Try again later.")

    @commands.command(description='Change the password of a DCS server', aliases=['passwd'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def password(self, ctx, coalition: Optional[str] = None):
        server = await utils.get_server(self, ctx)
        if server:
            if not coalition:
                if server['status'] in [Status.SHUTDOWN, Status.STOPPED]:
                    password = await utils.input_value(self, ctx, 'Please enter the new password (. for none):', True)
                    utils.changeServerSettings(server['server_name'], 'password', password)
                    await self.bot.audit(f"changed password", user=ctx.message.author, server=server)
                    await ctx.send('Password has been changed.')
                else:
                    await ctx.send(f"Server \"{server['server_name']}\" has to be stopped or shut down to change the password.")
            elif coalition.casefold() in ['red', 'blue']:
                if server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                    password = await utils.input_value(self, ctx, 'Please enter the new password (. for none):', True)
                    self.bot.sendtoDCS(server, {
                        "command": "setCoalitionPassword",
                        ("redPassword" if coalition.casefold() == 'red' else "bluePassword"): password or ''
                    })
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('UPDATE servers SET {} = %s WHERE server_name = %s'.format('blue_password' if coalition.casefold() == 'blue' else 'red_password'), (password, server['server_name']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.log.exception(error)
                        conn.rollback()
                    finally:
                        self.pool.putconn(conn)
                    await self.bot.audit(f"changed password for coalition {coalition}",
                                         user=ctx.message.author, server=server)
                    if server['status'] != Status.STOPPED and \
                            await utils.yn_question(self, ctx, "Password has been changed.\nDo you want the servers "
                                                               "to be restarted for the change to take effect?"):
                        self.bot.sendtoDCS(server, {"command": "stop_server"})
                        while server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
                            await asyncio.sleep(1)
                        self.bot.sendtoDCS(server, {"command": "start_server"})
                        await self.bot.audit('restarted the server', server=server, user=ctx.message.author)
                else:
                    await ctx.send(f"Server \"{server['server_name']}\" must not be shut down to change coalition "
                                   f"passwords.")
            else:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}password [red|blue]")

    @commands.command(description='Kick a user by name', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def kick(self, ctx, name, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if len(args) > 0:
                reason = ' '.join(args)
            else:
                reason = 'n/a'
            self.bot.sendtoDCS(server, {"command": "kick", "name": name, "reason": reason})
            await ctx.send(f'User "{name}" kicked.')
            await self.bot.audit(f'kicked player {name}' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                 user=ctx.message.author)

    @commands.command(description='Bans a user by ucid or discord id', usage='<member / ucid> [reason]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def ban(self, ctx, user: Union[discord.Member, str], *args):
        if len(args) > 0:
            reason = ' '.join(args)
        else:
            reason = 'n/a'
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.globals.values():
                        self.bot.sendtoDCS(server, {
                            "command": "ban",
                            "ucid": ucid,
                            "reason": reason
                        })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unban(self, ctx, user: Union[discord.Member, str]):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.globals.values():
                        self.bot.sendtoDCS(server, {"command": "unban", "ucid": ucid})
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Moves a user to spectators', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def spec(self, ctx, name, *args):
        server = await utils.get_server(self, ctx)
        if server:
            reason = ' '.join(args) if len(args) > 0 else None
            player = utils.get_player(self, server['server_name'], name=name, active=True)
            if player:
                self.bot.sendtoDCS(server, {
                    "command": "force_player_slot",
                    "playerID": player['id']
                })
                if reason:
                    self.bot.sendtoDCS(server, {
                        "command": "sendChatMessage",
                        "channel": ctx.channel.id,
                        "message": "You have been moved to spectators." + (f" Reason: {reason}" if reason else ""),
                        "from": ctx.message.author.display_name
                    })
                await ctx.send(f'User "{name}" moved to spectators.')
                await self.bot.audit(f'moved player {name} to spectators' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                     user=ctx.message.author)
            else:
                await ctx.send(f"Player {name} not found.")

    @commands.command(description='DMs the current DCS server log')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def dcslog(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            channel = await ctx.message.author.create_dm()
            path = os.path.expandvars(self.config[server['installation']]['DCS_HOME']) + r'\logs\dcs.log'
            if os.path.getsize(path) >= 8*1024*1024:
                with ZipFile('dcs.log.zip', 'w') as zipfile:
                    zipfile.write(path)
                filename = zipfile.filename
            else:
                filename = path
            try:
                await channel.send(content=f"This is the DCS logfile of server {server['server_name']}",
                                   file=discord.File(filename))
            finally:
                if filename.endswith('.zip'):
                    os.remove(filename)

    @commands.command(description='DMs the current bot log')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def botlog(self, ctx):
        channel = await ctx.message.author.create_dm()
        path = r'.\dcsserverbot.log'
        if os.path.getsize(path) >= 8 * 1024 * 1024:
            with ZipFile('dcsserverbot.log.zip', 'w') as zipfile:
                zipfile.write(path)
            filename = zipfile.filename
        else:
            filename = path
        try:
            await channel.send(content=f"This is the current DCSServerBot log of agent {platform.node()}",
                               file=discord.File(filename))
        finally:
            if filename.endswith('.zip'):
                os.remove(filename)

    @commands.command(description='Runs a shell command', hidden=True)
    @utils.has_role('Admin')
    @commands.guild_only()
    async def shell(self, ctx, *params):
        server = await utils.get_server(self, ctx)
        if server:
            cmd = ' '.join(params)
            await self.bot.audit(f"executed a shell command: ```{cmd}```", server=server, user=ctx.message.author)
            subprocess.run(shlex.split(cmd), shell=True)

    @commands.command(description='Starts a stopped DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def start(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] == Status.STOPPED:
                self.bot.sendtoDCS(server, {"command": "start_server"})
                await ctx.send(f"Starting server {server['server_name']} ...")
                await self.bot.audit('started the server', server=server, user=ctx.message.author)
            elif server['status'] == Status.SHUTDOWN:
                await ctx.send(f"Server {server['server_name']} is shut down. Use {self.config['BOT']['COMMAND_PREFIX']}startup to start it up.")
            elif server['status'] in [Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"Server {server['server_name']} is already started.")
            else:
                await ctx.send(f"Server {server['server_name']} is still {server['status'].name}, please wait ...")

    @commands.command(description='Stops a DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def stop(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] in [Status.RUNNING, Status.PAUSED]:
                self.bot.sendtoDCS(server, {"command": "stop_server"})
                await self.bot.audit('stopped the server', server=server, user=ctx.message.author)
                while server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
                    await asyncio.sleep(1)
                await ctx.send(f"Server {server['server_name']} stopped.")
            elif server['status'] == Status.STOPPED:
                await ctx.send(
                    f"Server {server['server_name']} is stopped already. Use {self.config['BOT']['COMMAND_PREFIX']}shutdown to terminate the dcs.exe process.")
            elif server['status'] == Status.SHUTDOWN:
                await ctx.send(f"Server {server['server_name']} is shut down already.")
            else:
                await ctx.send(f"Server {server['server_name']} is {server['status'].name}, please wait ...")

    @commands.command(description='Status of a DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def status(self, ctx):
        server = await utils.get_server(self, ctx)
        embed = discord.Embed(title=f"Server Status ({platform.node()})", color=discord.Color.blue())
        names = []
        status = []
        if server:
            names.append(server['server_name'])
            status.append(string.capwords(server['status'].name.lower()))
        else:
            for server in self.globals.values():
                names.append(server['server_name'])
                status.append(string.capwords(server['status'].name.lower()))
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            await ctx.send(embed=embed)

    @tasks.loop(minutes=1.0)
    async def update_bot_status(self):
        for server_name, server in self.globals.items():
            if server['status'] in const.STATUS_EMOJI.keys():
                try:
                    await self.bot.change_presence(
                        activity=discord.Game(const.STATUS_EMOJI[server['status']] + ' ' +
                                              re.sub(self.config['FILTER']['SERVER_FILTER'], '', server_name).strip()))
                    await asyncio.sleep(10)
                except Exception as ex:
                    self.log.debug("Exception in update_bot_status(): " + str(ex))

    @tasks.loop(minutes=5.0)
    async def check_for_dcs_update(self):
        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            branch, old_version = utils.getInstalledVersion(self.config['DCS']['DCS_INSTALLATION'])
            new_version = await utils.getLatestVersion(branch)
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                await self.do_update([300, 120, 60])
        except Exception as ex:
            self.log.debug("Exception in check_for_dcs_update(): " + str(ex))

    @check_for_dcs_update.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def process_message(self, message):
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    if message.attachments[0].filename.endswith('.json'):
                        data = await response.json(encoding="utf-8")
                        if 'configs' in data:
                            plugin = message.attachments[0].filename[:-5]
                            if plugin not in self.bot.plugins:
                                await message.channel.send(f"Plugin {string.capwords(plugin)} is not activated.")
                                return
                            with open(f"config/{plugin}.json", 'w', encoding="utf-8") as outfile:
                                json.dump(data, outfile, indent=2)
                            self.bot.reload(plugin)
                            await message.channel.send(f"Plugin {string.capwords(plugin)} re-configured.")
                    else:
                        with open('config/dcsserverbot.ini', 'w', encoding='utf-8') as outfile:
                            outfile.writelines('\n'.join((await response.text(encoding='utf-8')).splitlines()))
                        self.bot.config = utils.config = utils.reload()
                        await message.channel.send('dcsserverbot.ini updated.')
                        ctx = utils.ContextWrapper(message=message)
                        if await utils.yn_question(self, ctx, 'Do you want to restart the bot?'):
                            exit(-1)
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages or messages that does not contain json attachments
        if message.author.bot or not message.attachments or \
                not (
                        message.attachments[0].filename.endswith('.json') or
                        message.attachments[0].filename == 'dcsserverbot.ini'
                ):
            return
        # only Admin role is allowed to upload json files in channels
        if not await utils.get_server(self, message) or not utils.check_roles(['Admin'], message.author):
            return
        try:
            await self.process_message(message)
        finally:
            await message.delete()


class Master(Agent):

    @commands.command(description='Prune unused data in the database', hidden=True)
    @utils.has_role('Admin')
    @commands.guild_only()
    async def prune(self, ctx):
        if not await utils.yn_question(self, ctx, 'This will remove old data from your database and compact it.\nAre '
                                                  'you sure?'):
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # delete non-members that haven't a name with them (very old data)
                cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                               'discord_id = -1 AND name IS NULL)')
                cursor.execute('DELETE FROM players WHERE discord_id = -1 AND name IS NULL')
                # delete players that haven't shown up for 6 month
                cursor.execute("DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE last_seen "
                               "IS NULL OR last_seen < NOW() - interval '6 month')")
                cursor.execute("DELETE FROM players WHERE last_seen IS NULL OR last_seen < NOW() - interval '6 month'")
            conn.commit()
            await self.bot.audit(f'pruned the database', user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Bans a user by ucid or discord id', usage='<member / ucid> [reason]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def ban(self, ctx, user: Union[discord.Member, str], *args):
        if len(args) > 0:
            reason = ' '.join(args)
        else:
            reason = 'n/a'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                                   (ucid, ctx.message.author.display_name, reason))
                conn.commit()
                await super().ban(self, ctx, user, *args)
            await ctx.send('Player {} banned.'.format(user))
            await self.bot.audit(f'banned ' +
                                 (f'member {user.display_name}' if isinstance(user, discord.Member) else f' ucid {user}') +
                                 (f' with reason "{reason}"' if reason != 'n/a' else ''),
                                 user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unban(self, ctx, user: Union[discord.Member, str]):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('DELETE FROM bans WHERE ucid = %s', (ucid, ))
                conn.commit()
                await super().unban(self, ctx, user)
            await ctx.send('Player {} unbanned.'.format(user))
            await self.bot.audit(f'unbanned ' +
                                 (f'member {user.display_name}' if isinstance(user, discord.Member) else f' ucid {user}'),
                                 user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def format_bans(self, rows):
        embed = discord.Embed(title='List of Bans', color=discord.Color.blue())
        ucids = names = reasons = ''
        for ban in rows:
            if ban['discord_id'] != -1:
                user = self.bot.get_user(ban['discord_id'])
            else:
                user = None
            names += (user.name if user else ban['name'] if ban['name'] else '<unknown>') + '\n'
            ucids += ban['ucid'] + '\n'
            reasons += ban['reason'] + '\n'
        embed.add_field(name='UCID', value=ucids)
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Reason', value=reasons)
        return embed

    @commands.command(description='Shows active bans')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def bans(self, ctx):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT b.ucid, COALESCE(p.discord_id, -1) AS discord_id, p.name, b.banned_by, '
                               'b.reason FROM bans b LEFT OUTER JOIN players p on b.ucid = p.ucid')
                rows = list(cursor.fetchall())
                await utils.pagination(self, ctx, rows, self.format_bans, 20)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if self.config.getboolean('BOT', 'AUTOBAN'):
                    self.bot.log.debug(f'- Auto-ban member {member.display_name} on the DCS servers')
                    cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                                   'players WHERE discord_id = %s ON CONFLICT DO NOTHING', (member.id, ))
                    self.eventlistener.updateBans()
                self.bot.log.debug(f'- Delete stats of member {member.display_name}')
                cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                               'discord_id = %s)', (member.id, ))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                self.bot.log.debug(f'- Ban member {member.display_name} on the DCS servers.')
                cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                               'players WHERE discord_id = %s ON CONFLICT DO NOTHING', (member.id, ))
                self.eventlistener.updateBans()
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self.bot.log.debug('Member {} has joined guild {}'.format(member.display_name, member.guild.name))
        if self.config.getboolean('BOT', 'AUTOBAN') is True:
            self.bot.log.debug('Remove possible bans from DCS servers.')
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    # auto-unban them if they were auto-banned
                    cursor.execute('DELETE FROM bans WHERE ucid IN (SELECT ucid FROM players WHERE '
                                   'discord_id = %s)', (member.id, ))
                    self.eventlistener.updateBans()
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if 'GREETING_DM' in self.config['BOT']:
            channel = await member.create_dm()
            await channel.send(self.config['BOT']['GREETING_DM'].format(name=member.name, guild=member.guild.name))

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages or messages that does not contain json attachments
        if message.author.bot or not message.attachments or \
                not (
                        message.attachments[0].filename.endswith('.json') or
                        message.attachments[0].filename == 'dcsserverbot.ini'
                ):
            return
        # only Admin role is allowed to upload json files in channels
        if not utils.check_roles(['Admin'], message.author):
            return
        try:
            await super().process_message(message)
            async with aiohttp.ClientSession() as session:
                async with session.get(message.attachments[0].url) as response:
                    if response.status == 200:
                        data = await response.json(encoding="utf-8")
                        if 'configs' not in data:
                            embed = utils.format_embed(data)
                            msg = None
                            if 'message_id' in data:
                                with suppress(discord.errors.NotFound):
                                    msg = await message.channel.fetch_message(int(data['message_id']))
                            if msg:
                                await msg.edit(embed=embed)
                            else:
                                await message.channel.send(embed=embed)
                    else:
                        await message.channel.send(f'Error {response.status} while reading JSON file!')
        finally:
            await message.delete()


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Master(bot, AdminEventListener))
    else:
        bot.add_cog(Agent(bot, AdminEventListener))
