# commands.py
import asyncio
import discord
import os
import platform
import psycopg2
import psycopg2.extras
import re
import subprocess
from contextlib import closing
from core import utils, DCSServerBot, Plugin, Report
from core.const import Status
from discord.ext import commands, tasks
from typing import Union
from .listener import AdminEventListener


class Agent(Plugin):

    STATUS_EMOJI = {
        Status.LOADING: '🔄',
        Status.PAUSED: '⏸️',
        Status.RUNNING: '▶️',
        Status.STOPPED: '⏹️'
    }

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_bot_status.start()

    def cog_unload(self):
        self.update_bot_status.cancel()
        super().cog_unload()

    @commands.command(description='Lists the registered DCS servers')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def servers(self, ctx):
        if len(self.globals) > 0:
            for server_name, server in self.globals.items():
                if server['status'] in [Status.RUNNING, Status.PAUSED]:
                    players = self.bot.player_data[server['server_name']]
                    num_players = len(players[players['active'] == True]) + 1
                    report = Report(self.bot, 'mission', 'serverStatus.json')
                    env = await report.render(server=server, num_players=num_players)
                    await ctx.send(embed=env.embed)
        else:
            await ctx.send('No server running on host {}'.format(platform.node()))

    @commands.command(description='Starts a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def startup(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            installation = server['installation']
            if server['status'] in [Status.STOPPED, Status.SHUTDOWN]:
                await ctx.send('DCS server "{}" starting up ...'.format(server['server_name']))
                utils.start_dcs(self, server)
                server['status'] = Status.LOADING
                # set maintenance flag to prevent auto-stops of this server
                server['maintenance'] = True
                await self.bot.audit(
                    f"User {ctx.message.author.display_name} started DCS server \"{server['server_name']}\".")
            else:
                await ctx.send('DCS server "{}" is already started.'.format(server['server_name']))
            if 'SRS_CONFIG' in self.config[installation]:
                if not utils.is_open(self.config[installation]['SRS_HOST'], self.config[installation]['SRS_PORT']):
                    if await utils.yn_question(self, ctx, 'Do you want to start the DCS-SRS server "{}"?'.format(server['server_name'])) is True:
                        await ctx.send('DCS-SRS server "{}" starting up ...'.format(server['server_name']))
                        utils.start_srs(self, server)
                        await self.bot.audit(
                            f"User {ctx.message.author.display_name} started DCS-SRS server \"{server['server_name']}\".")
                else:
                    await ctx.send('DCS-SRS server "{}" is already started.'.format(server['server_name']))

    @commands.command(description='Shutdown a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def shutdown(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            installation = server['installation']
            if server['status'] in [Status.UNKNOWN, Status.LOADING]:
                await ctx.send('Server is currently starting up. Please wait and try again.')
            elif server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
                if await utils.yn_question(self, ctx, 'Do you want to shut down the '
                                                      'DCS server "{}"?'.format(server['server_name'])) is True:
                    await ctx.send('Shutting down DCS server "{}" ...'.format(server['server_name']))
                    utils.stop_dcs(self, server)
                    server['status'] = Status.SHUTDOWN
                    # set maintenance flag to prevent auto-starts of this server
                    server['maintenance'] = True
                    await self.bot.audit(
                        f"User {ctx.message.author.display_name} shut DCS server \"{server['server_name']}\" down.")
            else:
                await ctx.send('DCS server {} is already shut down.'.format(server['server_name']))
            if 'SRS_CONFIG' in self.config[installation]:
                if utils.check_srs(self, server):
                    if await utils.yn_question(self, ctx, 'Do you want to shut down the '
                                                          'DCS-SRS server "{}"?'.format(server['server_name'])) is True:
                        if utils.stop_srs(self, server):
                            await ctx.send('DCS-SRS server "{}" shutdown.'.format(server['server_name']))
                            await self.bot.audit(f"User {ctx.message.author.display_name} shut "
                                                 f"DCS-SRS server \"{server['server_name']}\" down.")
                        else:
                            await ctx.send('Shutdown of DCS-SRS server "{}" failed.'.format(server['server_name']))
                else:
                    await ctx.send('DCS-SRS server {} is already shut down.'.format(server['server_name']))

    @commands.command(description='Update a DCS Installation')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def update(self, ctx):
        # check versions
        branch, old_version = utils.getInstalledVersion(self.config['DCS']['DCS_INSTALLATION'])
        new_version = await utils.getLatestVersion(branch)
        if old_version == new_version:
            await ctx.send('Your installed version {} is the latest on branch {}.'.format(old_version, branch))
        else:
            if await utils.yn_question(self, ctx, 'Would you like to update from version {} to {}?\nAll running '
                                                  'DCS servers will be shut down!'.format(old_version,
                                                                                          new_version)) is True:
                await self.bot.audit(f"User {ctx.message.author.display_name} started an update of all DCS "
                                     f"servers on node {platform.node()}.")
                servers = []
                for server_name, server in self.globals.items():
                    if server['status'] in [Status.RUNNING, Status.PAUSED]:
                        servers.append(server)
                        self.bot.sendtoDCS(server, {"command": "shutdown"})
                        await ctx.send(f'Shutting down DCS server "{server_name}" ...')
                        server['status'] = Status.SHUTDOWN
                        server['maintenance'] = True
                # give the DCS servers some time to shut down.
                if len(servers):
                    await asyncio.sleep(10)
                await ctx.send('Updating DCS World ...')
                subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
                    self.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs_updater.exe')
                utils.sanitize(self)
                await ctx.send(f'DCS World updated to version {new_version}')
                if await utils.yn_question(self, ctx, 'Would you like to restart your DCS servers?') is True:
                    for server in servers:
                        await ctx.send(f"Starting DCS server \"{server['server_name']}\" ...")
                        utils.start_dcs(self, server)
                        server['status'] = Status.LOADING
                        del server['maintenance']
                    await ctx.send('All DCS servers restarted.')

    @commands.command(description='Change the password of a DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def password(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] == Status.SHUTDOWN:
                msg = await ctx.send('Please enter the new password: ')
                response = await self.bot.wait_for('message', timeout=300.0)
                password = response.content
                await msg.delete()
                await response.delete()
                utils.changeServerSettings(server['server_name'], 'password', password)
                await ctx.send('Password has been changed.')
                await self.bot.audit(f"User {ctx.message.author.display_name} changed the password "
                                     f"of server \"{server['server_name']}\".")
            else:
                await ctx.send('Server "{}" has to be shut down to change the password.'.format(server['server_name']))

    @commands.command(description='Kick a user by ucid', usage='<ucid>')
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
            await self.bot.audit(f'User {ctx.message.author.display_name} kicked player {name}' +
                                 (f' with reason "{reason}".' if reason != 'n/a' else '.'))

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

    @commands.command(description='Unregisters the server from this instance')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def unregister(self, ctx, node=platform.node()):
        server = await utils.get_server(self, ctx)
        if server:
            server_name = server['server_name']
            if server['status'] in [Status.STOPPED, Status.SHUTDOWN]:
                if await utils.yn_question(self, ctx, 'Are you sure to unregister server "{}" from '
                                                      'node "{}"?'.format(server_name, node)) is True:
                    self.bot.embeds.pop(server_name)
                    await ctx.send('Server {} unregistered.'.format(server_name))
                    await self.bot.audit(
                        f"User {ctx.message.author.display_name} unregistered DCS server \"{server['server_name']}\".")
                else:
                    await ctx.send('Aborted.')
            else:
                await ctx.send('Please stop server "{}" before unregistering!'.format(server_name))

    @commands.command(description='Rename a server')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def rename(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server:
            oldname = server['server_name']
            newname = ' '.join(args)
            if server['status'] in [Status.STOPPED, Status.SHUTDOWN]:
                conn = self.pool.getconn()
                try:
                    if await utils.yn_question(self, ctx, 'Are you sure to rename server "{}" '
                                                          'to "{}"?'.format(oldname, newname)) is True:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                                           (newname, oldname))
                            cursor.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                                           (newname, oldname))
                            cursor.execute('UPDATE missions SET server_name = %s WHERE server_name = %s',
                                           (newname, oldname))
                            conn.commit()
                        utils.changeServerSettings(server['server_name'], 'name', newname)
                        server['server_name'] = newname
                        self.bot.embeds[newname] = self.bot.embeds[oldname]
                        self.bot.embeds.pop(oldname)
                        await ctx.send('Server has been renamed.')
                        await self.bot.audit(
                            f'User {ctx.message.author.display_name} renamed DCS server "{oldname}" to "{newname}".')
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
            else:
                await ctx.send('Please stop server "{}" before renaming!'.format(oldname))

    @tasks.loop(minutes=1.0)
    async def update_bot_status(self):
        for server_name, server in self.globals.items():
            if server['status'] in self.STATUS_EMOJI.keys():
                await self.bot.change_presence(activity=discord.Game(self.STATUS_EMOJI[server['status']] + ' ' +
                                                                     re.sub(self.config['FILTER']['SERVER_FILTER'],
                                                                            '', server_name).strip()))
                await asyncio.sleep(10)


class Master(Agent):

    @commands.command(description='Prune unused data in the database', hidden=True)
    @utils.has_role('Admin')
    @commands.guild_only()
    async def prune(self, ctx):
        if not await utils.yn_question(self, ctx, 'This will remove unused data from your database and compact '
                                                  'it.\nAre you sure?'):
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('CREATE TEMPORARY TABLE temp_players (discord_id BIGINT)')
                cursor.execute('CREATE TEMPORARY TABLE temp_missions (id SERIAL PRIMARY KEY, server_name TEXT NOT '
                               'NULL, mission_name TEXT NOT NULL, mission_theatre TEXT NOT NULL, mission_start '
                               'TIMESTAMP NOT NULL DEFAULT NOW(), mission_end TIMESTAMP)')
                cursor.execute('CREATE TEMPORARY TABLE temp_statistics (mission_id INTEGER NOT NULL, player_ucid TEXT '
                               'NOT NULL, slot TEXT NOT NULL, kills INTEGER DEFAULT 0, pvp INTEGER DEFAULT 0, '
                               'deaths INTEGER DEFAULT 0, ejections INTEGER DEFAULT 0, crashes INTEGER DEFAULT 0, '
                               'teamkills INTEGER DEFAULT 0, kills_planes INTEGER DEFAULT 0, kills_helicopters '
                               'INTEGER DEFAULT 0, kills_ships INTEGER DEFAULT 0, kills_sams INTEGER DEFAULT 0, '
                               'kills_ground INTEGER DEFAULT 0, deaths_pvp INTEGER DEFAULT 0, deaths_planes INTEGER '
                               'DEFAULT 0, deaths_helicopters INTEGER DEFAULT 0, deaths_ships INTEGER DEFAULT 0, '
                               'deaths_sams INTEGER DEFAULT 0, deaths_ground INTEGER DEFAULT 0, takeoffs INTEGER '
                               'DEFAULT 0, landings INTEGER DEFAULT 0, hop_on TIMESTAMP NOT NULL DEFAULT NOW(), '
                               'hop_off TIMESTAMP, PRIMARY KEY (mission_id, player_ucid, slot, hop_on))')
                for member in self.bot.guilds[0].members:
                    cursor.execute('INSERT INTO temp_players VALUES (%s)', (member.id, ))
                cursor.execute('SELECT COUNT(*) FROM statistics s, players p WHERE s.player_ucid = p.ucid AND '
                               'p.discord_id NOT IN (SELECT discord_id FROM temp_players)')
                prune_statistics = cursor.fetchone()[0]
                cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                               'discord_id NOT IN (SELECT discord_id FROM temp_players))')
                await ctx.send(f'{prune_statistics} statistics pruned.')
                cursor.execute('SELECT COUNT(*) FROM players WHERE discord_id NOT IN (SELECT discord_id FROM '
                               'temp_players)')
                prune_players = cursor.fetchone()[0]
                cursor.execute('DELETE FROM players WHERE discord_id NOT IN (SELECT discord_id FROM temp_players)')
                await ctx.send(f'{prune_players} players pruned.')
                cursor.execute('SELECT COUNT(*) FROM missions WHERE id NOT IN (SELECT mission_id FROM statistics)')
                prune_missions = cursor.fetchone()[0]
                cursor.execute('DELETE FROM missions WHERE id NOT IN (SELECT mission_id FROM statistics)')
                await ctx.send(f'{prune_missions} missions pruned.')
                cursor.execute('INSERT INTO temp_missions SELECT MIN(id), server_name, mission_name, mission_theatre, '
                               'MIN(mission_start), MIN(mission_start) + SUM(mission_end - mission_start) as runtime '
                               'FROM missions GROUP BY server_name, mission_name, mission_theatre')
                cursor.execute('SELECT count(*) FROM missions')
                missions_old = cursor.fetchone()[0]
                cursor.execute('SELECT count(*) FROM temp_missions')
                missions_new = cursor.fetchone()[0]
                await ctx.send(f'{missions_old - missions_new} ({missions_old}) missions aggregated.')
                cursor.execute('INSERT INTO temp_statistics SELECT tm.id AS mission_id, player_ucid, slot, sum(kills) '
                               'as kills, sum(pvp) as pvp, sum(deaths) as death, sum(ejections) as ejections, '
                               'sum(crashes) as crashes, sum(teamkills) as teamkills, sum(kills_planes) as '
                               'kills_planes, sum(kills_helicopters) as kills_helicopters, sum(kills_ships) as '
                               'kills_ships, sum(kills_sams) as kills_sams, sum(kills_ground) as kills_ground, '
                               'sum(deaths_pvp) as deaths_pvp, sum(deaths_planes) as deaths_planes, '
                               'sum(deaths_helicopters) as deaths_helicopters, sum(deaths_ships) as deaths_ships, '
                               'sum(deaths_sams) as deaths_sams, sum(deaths_ground) as deaths_groups, sum(takeoffs) '
                               'as takeoffs, sum(landings) as landings, MIN(hop_on) as hop_on, MIN(hop_on) + SUM('
                               'hop_off - hop_on) FROM statistics s, missions m, temp_missions tm WHERE m.id = '
                               's.mission_id AND m.server_name = tm.server_name AND m.mission_name = tm.mission_name '
                               'AND m.mission_theatre = tm.mission_theatre group by tm.id, player_ucid, slot')
                cursor.execute('SELECT count(*) FROM statistics')
                statistics_old = cursor.fetchone()[0]
                cursor.execute('SELECT count(*) FROM temp_statistics')
                statistics_new = cursor.fetchone()[0]
                await ctx.send(f'{statistics_old - statistics_new} ({statistics_old}) statistics aggregated.')
                cursor.execute('DELETE FROM missions')
                cursor.execute('INSERT INTO missions SELECT * FROM temp_missions')
                cursor.execute('DELETE FROM statistics')
                cursor.execute('INSERT INTO statistics SELECT * FROM temp_statistics')
                # TODO: REMOVE THIS !!!
                conn.rollback()
                # TODO: REMOVE THIS !!!
                cursor.execute('DROP TABLE temp_players')
                cursor.execute('DROP TABLE temp_missions')
                cursor.execute('DROP TABLE temp_statistics')
                conn.commit()
                await self.bot.audit(f'User {ctx.message.author.display_name} pruned the database.')
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
            await self.bot.audit(f'User {ctx.message.author.display_name} banned ' +
                                 (f'member {user.display_name}' if isinstance(user, discord.Member) else f' ucid {user}') +
                                 (f' with reason "{reason}"' if reason != 'n/a' else ''))
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
            await self.bot.audit(f'User {ctx.message.author.display_name} unbanned ' +
                                 (f'member {user.display_name}' if isinstance(user, discord.Member) else f' ucid {user}'))
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
        self.bot.log.debug('Member {} has left guild {} - ban them on DCS servers (optional) '
                           'and delete their stats.'.format(member.display_name, member.guild.name))
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
                    cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                                   'players WHERE discord_id = %s', (member.id, ))
                cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                               'discord_id = %s)', (member.id, ))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # try to match new users with existing but unmatched DCS users
                ucid = utils.match_user(self, member)
                if ucid:
                    cursor.execute(
                        'UPDATE players SET discord_id = %s WHERE ucid = %s AND discord_id = -1', (member.id, ucid))
                    await self.bot.audit(f"New member {member.display_name} could be matched to ucid {ucid}.")
                else:
                    await self.bot.audit(f"New member {member.display_name} could not be matched to a DCS user.")
                # auto-unban them if they were auto-banned
                if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
                    self.bot.log.debug('Member {} has joined guild {} - remove possible '
                                       'bans from DCS servers.'.format(member.display_name, member.guild.name))
                    cursor.execute('DELETE FROM bans WHERE ucid IN (SELECT ucid FROM players WHERE '
                                   'discord_id = %s)', (member.id, ))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)
        self.eventlistener.updateBans()


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Master(bot, AdminEventListener))
    else:
        bot.add_cog(Agent(bot, AdminEventListener))
