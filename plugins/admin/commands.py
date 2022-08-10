# noinspection PyPackageRequirements
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
from core import utils, DCSServerBot, Plugin, Report, Player, Status, Server, Coalition
from discord.ext import commands, tasks
from typing import Union, List, Optional
from zipfile import ZipFile
from .listener import AdminEventListener


STATUS_EMOJI = {
    Status.LOADING: '🔄',
    Status.PAUSED: '⏸️',
    Status.RUNNING: '▶️',
    Status.STOPPED: '⏹️'
}


class Agent(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_pending = False
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.start()

    def cog_unload(self):
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.cancel()
        super().cog_unload()

    @commands.command(description='Lists the registered DCS servers')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def servers(self, ctx):
        if len(self.bot.servers) > 0:
            for server_name, server in self.bot.servers.items():
                if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                    players = server.get_active_players()
                    num_players = len(players) + 1
                    report = Report(self.bot, 'mission', 'serverStatus.json')
                    env = await report.render(server=server, num_players=num_players)
                    await ctx.send(embed=env.embed)
        else:
            await ctx.send('No server running on host {}'.format(platform.node()))

    async def do_update(self, warn_times: List[int], ctx=None):
        async def shutdown_with_warning(server: Server):
            if server.is_populated():
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            server.sendPopupMessage(Coalition.ALL, f'Server is going down for a DCS update in '
                                                                   f'{utils.format_time(warn_time)}!')
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await server.shutdown()

        self.update_pending = True
        if ctx:
            await ctx.send('Shutting down DCS servers, warning users before ...')
        else:
            self.log.info('Shutting down DCS servers, warning users before ...')
        servers = []
        tasks = []
        for server_name, server in self.bot.servers.items():
            if server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if server.maintenance:
                servers.append(server)
            else:
                server.maintenance = True
                tasks.append(asyncio.create_task(shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if len(tasks):
            await asyncio.gather(*tasks)
        if ctx:
            await ctx.send('Updating DCS World. Please wait, this might take some time ...')
        else:
            self.log.info('Updating DCS World ...')
        for plugin in self.bot.cogs.values():
            await plugin.before_dcs_update()
        # disable any popup on the remote machine
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
            self.bot.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs_updater.exe', startupinfo=startupinfo)
        utils.sanitize(self)
        # run after_dcs_update() in all plugins
        for plugin in self.bot.cogs.values():
            await plugin.after_dcs_update()
        message = None
        if ctx:
            await ctx.send('DCS World updated to the latest version.')
            message = await ctx.send('Starting up DCS servers again ...')
        else:
            self.log.info('DCS World updated to the latest version.\nStarting up DCS servers again ...')
        for server_name, server in self.bot.servers.items():
            if server not in servers:
                # let the scheduler do its job
                server.maintenance = False
            else:
                # the server was running before (being in maintenance mode), so start it again
                await server.startup()
        self.update_pending = False
        if message:
            await message.delete()
            await ctx.send('DCS servers started.')

    @commands.command(description='Update a DCS Installation')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def update(self, ctx, param: Optional[str] = None):
        if self.update_pending:
            await ctx.send('An update is already running, please wait ...')
            return
        # check versions
        branch, old_version = utils.getInstalledVersion(self.bot.config['DCS']['DCS_INSTALLATION'])
        new_version = await utils.getLatestVersion(branch)
        if old_version == new_version:
            await ctx.send('Your installed version {} is the latest on branch {}.'.format(old_version, branch))
        elif new_version or param and param == '-force':
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
        server: Server = await self.bot.get_server(ctx)
        if server:
            if not coalition:
                if server.status in [Status.SHUTDOWN, Status.STOPPED]:
                    password = await utils.input_value(self, ctx, 'Please enter the new password (. for none):', True)
                    server.changeServerSettings('password', password)
                    await self.bot.audit(f"changed password", user=ctx.message.author, server=server)
                    await ctx.send('Password has been changed.')
                else:
                    await ctx.send(f"Server \"{server.name}\" has to be stopped or shut down to change the password.")
            elif coalition.casefold() in ['red', 'blue']:
                if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                    password = await utils.input_value(self, ctx, 'Please enter the new password (. for none):', True)
                    server.sendtoDCS({
                        "command": "setCoalitionPassword",
                        ("redPassword" if coalition.casefold() == 'red' else "bluePassword"): password or ''
                    })
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('UPDATE servers SET {} = %s WHERE server_name = %s'.format('blue_password' if coalition.casefold() == 'blue' else 'red_password'), (password, server.name))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.log.exception(error)
                        conn.rollback()
                    finally:
                        self.pool.putconn(conn)
                    await self.bot.audit(f"changed password for coalition {coalition}",
                                         user=ctx.message.author, server=server)
                    if server.status != Status.STOPPED and \
                            await utils.yn_question(self, ctx, "Password has been changed.\nDo you want the servers "
                                                               "to be restarted for the change to take effect?"):
                        await server.restart()
                        await self.bot.audit('restarted the server', server=server, user=ctx.message.author)
                else:
                    await ctx.send(f"Server \"{server.name}\" must not be shut down to change coalition "
                                   f"passwords.")
            else:
                await ctx.send(f"Usage: {ctx.prefix}password [red|blue]")

    @staticmethod
    def format_player_list(data: list[Player], marker, marker_emoji):
        embed = discord.Embed(title='Mission List', color=discord.Color.blue())
        ids = names = ucids = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            names += data[i].name + '\n'
            ucids += data[i].ucid + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Name', value=names)
        embed.add_field(name='UCID', value=ucids)
        embed.set_footer(text='Press a number to kick this user.')
        return embed

    @commands.command(description='Kick a user by name', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def kick(self, ctx, name, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if len(args) > 0:
                reason = ' '.join(args)
            else:
                reason = 'n/a'
            # find that player
            if server.status != Status.RUNNING:
                await ctx.send('Server is not running.')
                return
            players = [x for x in server.get_active_players() if name.casefold() in x.name.casefold()]
            if len(players) > 1:
                num = await utils.selection_list(self, ctx, players, self.format_player_list)
            elif len(players) == 1:
                num = 0
            else:
                await ctx.send(f"No player \"{name}\" found.")
                return
            if num >= 0:
                player = players[num]
                server.kick(player, reason)
                await ctx.send(f"User \"{player.name}\" kicked.")
                await self.bot.audit(f"kicked player {player.name}" + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                     user=ctx.message.author)
            else:
                await ctx.send('Aborted.')

    @commands.command(description='Bans a user by ucid or discord id', usage='<member|ucid> [reason]')
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
                    for server in self.bot.servers.values():
                        server.sendtoDCS({
                            "command": "ban",
                            "ucid": ucid,
                            "reason": reason
                        })
                        player = server.get_player(ucid=ucid)
                        if player:
                            player.banned = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member|ucid>')
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
                    for server in self.bot.servers.values():
                        server.sendtoDCS({"command": "unban", "ucid": ucid})
                        player = server.get_player(ucid=ucid)
                        if player:
                            player.banned = False
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Moves a user to spectators', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def spec(self, ctx, name, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            reason = ' '.join(args) if len(args) > 0 else None
            player = server.get_player(name=name, active=True)
            if player:
                server.move_to_spectators(player)
                if reason:
                    player.sendChatMessage(f"You have been moved to spectators. Reason: {reason}",
                                           ctx.message.author.display_name)
                await ctx.send(f'User "{name}" moved to spectators.')
                await self.bot.audit(f'moved player {name} to spectators' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                     user=ctx.message.author)
            else:
                await ctx.send(f"Player {name} not found.")

    @commands.command(description='DMs the current DCS server log')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def dcslog(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            channel = await ctx.message.author.create_dm()
            path = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME']) + r'\logs\dcs.log'
            if os.path.getsize(path) >= 8*1024*1024:
                with ZipFile('dcs.log.zip', 'w') as zipfile:
                    zipfile.write(path)
                filename = zipfile.filename
            else:
                filename = path
            try:
                await channel.send(content=f"This is the DCS logfile of server {server.name}",
                                   file=discord.File(filename))
                await ctx.send('Current dcs.log sent as DM.')
                await self.bot.audit(f'requested the dcs.log', server=server, user=ctx.message.author)
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
            await channel.send(content=f"This is the current DCSServerBot log of node {platform.node()}",
                               file=discord.File(filename))
            await ctx.send('Current dcsserverbot.log sent as DM.')
            await self.bot.audit(f'requested the dcsserverbot.log for node {platform.node()}', user=ctx.message.author)
        finally:
            if filename.endswith('.zip'):
                os.remove(filename)

    @commands.command(description='Runs a shell command', hidden=True)
    @utils.has_role('Admin')
    @commands.guild_only()
    async def shell(self, ctx, *params):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if len(params):
                cmd = shlex.split(' '.join(params))
                await self.bot.audit("executed a shell command: ```{}```".format(' '.join(cmd)), server=server,
                                     user=ctx.message.author)
                try:
                    p = subprocess.run(cmd, shell=True, capture_output=True, timeout=300)
                    await ctx.send('```' + p.stdout.decode('cp1252', 'ignore') + '```')
                except subprocess.TimeoutExpired:
                    await ctx.send('Timeout.')
            else:
                await ctx.send(f"Usage: {ctx.prefix}shell <command>")

    @commands.command(description='Starts a stopped DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def start(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status == Status.STOPPED:
                msg = await ctx.send(f"Starting server {server.name} ...")
                await server.start()
                await msg.delete()
                await ctx.send(f"Server {server.name} started.")
                await self.bot.audit('started the server', server=server, user=ctx.message.author)
            elif server.status == Status.SHUTDOWN:
                await ctx.send(f"Server {server.name} is shut down. Use {ctx.prefix}startup to start it up.")
            elif server.status in [Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"Server {server.name} is already started.")
            else:
                await ctx.send(f"Server {server.name} is still {server.status.name}, please wait ...")

    @commands.command(description='Stops a DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def stop(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status in [Status.RUNNING, Status.PAUSED]:
                if server.is_populated() and \
                        not await utils.yn_question(self, ctx, "People are flying on this server atm.\n"
                                                               "Do you really want to stop it?"):
                    return
                await server.stop()
                await self.bot.audit('stopped the server', server=server, user=ctx.message.author)
                await ctx.send(f"Server {server.name} stopped.")
            elif server.status == Status.STOPPED:
                await ctx.send(f"Server {server.name} is stopped already. Use {ctx.prefix}shutdown to terminate the "
                               f"dcs.exe process.")
            elif server.status == Status.SHUTDOWN:
                await ctx.send(f"Server {server.name} is shut down already.")
            else:
                await ctx.send(f"Server {server.name} is {server.status.name}, please wait ...")

    @commands.command(description='Status of a DCS server')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def status(self, ctx):
        embed = discord.Embed(title=f"Server Status ({platform.node()})", color=discord.Color.blue())
        names = []
        status = []
        maintenance = []
        for server in self.bot.servers.values():
            names.append(server.name)
            status.append(string.capwords(server.status.name.lower()))
            maintenance.append('Y' if server.maintenance else 'N')
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Maint.', value='\n'.join(maintenance))
            await ctx.send(embed=embed)

    @tasks.loop(minutes=5.0)
    async def check_for_dcs_update(self):
        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            branch, old_version = utils.getInstalledVersion(self.bot.config['DCS']['DCS_INSTALLATION'])
            new_version = await utils.getLatestVersion(branch)
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                await self.do_update([300, 120, 60])
        except Exception as ex:
            self.log.debug("Exception in check_for_dcs_update(): " + str(ex))

    @check_for_dcs_update.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def process_message(self, message) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    ctx = utils.ContextWrapper(message=message)
                    if message.attachments[0].filename.endswith('.json'):
                        data = await response.json(encoding="utf-8")
                        if 'configs' in data:
                            plugin = message.attachments[0].filename[:-5]
                            if plugin not in self.bot.plugins:
                                await message.channel.send(f"Plugin {string.capwords(plugin)} is not activated.")
                                return True
                            filename = f"config/{plugin}.json"
                            if os.path.exists(filename) and not \
                                    await utils.yn_question(self, ctx, f'Do you want to overwrite {filename} on node {platform.node()}?'):
                                await message.channel.send('Aborted.')
                                return True
                            with open(filename, 'w', encoding="utf-8") as outfile:
                                json.dump(data, outfile, indent=2)
                            self.bot.reload(plugin)
                            await message.channel.send(f"Plugin {string.capwords(plugin)} re-configured.")
                            return True
                        else:
                            return False
                    else:
                        if await utils.yn_question(self, ctx, f'Do you want to overwrite dcsserverbot.ini on node {platform.node()}?'):
                            with open('config/dcsserverbot.ini', 'w', encoding='utf-8') as outfile:
                                outfile.writelines('\n'.join((await response.text(encoding='utf-8')).splitlines()))
                            self.bot.config = utils.config = utils.reload()
                            await message.channel.send('dcsserverbot.ini updated.')
                            if await utils.yn_question(self, ctx, 'Do you want to restart the bot?'):
                                exit(-1)
                        else:
                            await message.channel.send('Aborted.')
                        return True
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')
                    return True

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
        if not await self.bot.get_server(message) or not utils.check_roles(['Admin'], message.author):
            return
        if await self.process_message(message):
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

    @commands.command(description='Bans a user by ucid or discord id', usage='<member|ucid> [reason]')
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
            if isinstance(user, discord.Member):
                await ctx.send(f'Member {user.display_name} banned.')
            else:
                await ctx.send(f'Player {user} banned.')
            await self.bot.audit(f'banned ' +
                                 (f'member {user.display_name}' if isinstance(user, discord.Member) else f' ucid {user}') +
                                 (f' with reason "{reason}"' if reason != 'n/a' else ''),
                                 user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member|ucid>')
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
            if isinstance(user, discord.Member):
                await ctx.send(f'Member {user.display_name} unbanned.')
            else:
                await ctx.send(f'Player {user} unbanned.')
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
                if self.bot.config.getboolean('BOT', 'AUTOBAN'):
                    self.bot.log.debug(f'- Auto-ban member {member.display_name} on the DCS servers')
                    cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                                   'players WHERE discord_id = %s ON CONFLICT DO NOTHING', (member.id, ))
                    self.eventlistener._updateBans()
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
                self.eventlistener._updateBans()
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self.bot.log.debug('Member {} has joined guild {}'.format(member.display_name, member.guild.name))
        if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
            self.bot.log.debug('Remove possible bans from DCS servers.')
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    # auto-unban them if they were auto-banned
                    cursor.execute('DELETE FROM bans WHERE ucid IN (SELECT ucid FROM players WHERE '
                                   'discord_id = %s)', (member.id, ))
                    self.eventlistener._updateBans()
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if 'GREETING_DM' in self.bot.config['BOT']:
            channel = await member.create_dm()
            await channel.send(self.bot.config['BOT']['GREETING_DM'].format(name=member.name, guild=member.guild.name))

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
        if await self.bot.get_server(message) and await super().process_message(message):
            await message.delete()
            return
        if not message.attachments[0].filename.endswith('.json'):
            return
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    data = await response.json(encoding="utf-8")
                    if 'configs' not in data:
                        embed = utils.format_embed(data)
                        msg = None
                        if 'message_id' in data:
                            try:
                                msg = await message.channel.fetch_message(int(data['message_id']))
                                await msg.edit(embed=embed)
                            except discord.errors.NotFound:
                                msg = None
                            except discord.errors.DiscordException as ex:
                                self.log.exception(ex)
                                await message.channel.send(f'Error while updating embed!')
                                return
                        if not msg:
                            await message.channel.send(embed=embed)
                        await message.delete()
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Master(bot, AdminEventListener))
    else:
        bot.add_cog(Agent(bot, AdminEventListener))
