import dateparser
import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, utils, Report, Status, Server, Coalition, Channel
from discord.ext import commands
from typing import Optional
from .listener import GameMasterEventListener


class GameMasterAgent(Plugin):

    def install(self):
        super().install()
        for server in self.bot.servers.values():
            if self.bot.config.getboolean(server.installation, 'COALITIONS'):
                self.log.debug(f'  - Updating "{server.name}":serverSettings.lua for coalitions')
                server.changeServerSettings('allow_players_pool', False)

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE campaigns SET server_name = %s WHERE server_name = %s', (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages
        if message.author.bot:
            return
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                continue
            if self.bot.config.getboolean(server.installation, 'COALITIONS'):
                sides = utils.get_sides(message, server)
                if Coalition.BLUE in sides and server.get_channel(Channel.COALITION_BLUE).id == message.channel.id:
                    # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.BLUE, message.content, message.author.display_name)
                    pass
                elif Coalition.RED in sides and server.get_channel(Channel.COALITION_RED).id == message.channel.id:
                    # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.RED, message.content, message.author.display_name)
                    pass
            if server.get_channel(Channel.CHAT) and server.get_channel(Channel.CHAT).id == message.channel.id:
                if message.content.startswith(self.bot.config['BOT']['COMMAND_PREFIX']) is False:
                    server.sendChatMessage(Coalition.ALL, message.content, message.author.display_name)

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @utils.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server: Server = await self.bot.get_server(ctx)
        if server and server.status == Status.RUNNING:
            server.sendtoDCS({
                "command": "sendChatMessage",
                "channel": ctx.channel.id,
                "message": ' '.join(args),
                "from": ctx.message.author.display_name
            })

    @commands.command(description='Sends a popup to a coalition', usage='<coal.|user> [time] <msg>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status != Status.RUNNING:
                await ctx.send(f"Mission is {server.status.name.lower()}, message discarded.")
                return
            if len(args) > 0:
                if args[0].isnumeric():
                    time = int(args[0])
                    i = 1
                else:
                    time = -1
                    i = 0
                message = ' '.join(args[i:])
                if to.lower() not in ['all', 'red', 'blue']:
                    player = server.get_player(name=to, active=True)
                    if player and len(player.slot) > 0:
                        player.sendPopupMessage(message, time, ctx.message.author.display_name)
                    else:
                        await ctx.send(f'Can\'t find player "{to}" or player is not in an active unit.')
                        return
                else:
                    server.sendPopupMessage(Coalition(to.lower()), message, time, ctx.message.author.display_name)
                await ctx.send('Message sent.')
            else:
                await ctx.send(f"Usage: {self.bot.config['BOT']['COMMAND_PREFIX']}popup all|red|blue|user [time] <message>")

    @commands.command(description='Set or clear a flag inside the mission', usage='<flag> [value]')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def flag(self, ctx, flag: int, value: int = None):
        server: Server = await self.bot.get_server(ctx)
        if server and server.status in [Status.RUNNING, Status.PAUSED]:
            if value is not None:
                server.sendtoDCS({
                    "command": "setFlag",
                    "channel": ctx.channel.id,
                    "flag": flag,
                    "value": value
                })
                await ctx.send(f"Flag {flag} set to {value}.")
            else:
                data = await server.sendtoDCSSync({"command": "getFlag", "flag": flag})
                await ctx.send(f"Flag {flag} has value {data['value']}.")
        else:
            await ctx.send(f"Mission is {server.status.name.lower()}, can't set/get flag.")

    @commands.command(description='Calls any function inside the mission', usage='<script>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script(self, ctx, *script):
        server: Server = await self.bot.get_server(ctx)
        if server and server.status in [Status.RUNNING, Status.PAUSED]:
            server.sendtoDCS({
                "command": "do_script",
                "script": ' '.join(script)
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server.status.name.lower()}, command discarded.")

    @commands.command(description='Loads a lua file into the mission', usage='<file>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script_file(self, ctx, filename):
        server: Server = await self.bot.get_server(ctx)
        if server and server.status in [Status.RUNNING, Status.PAUSED]:
            server.sendtoDCS({
                "command": "do_script_file",
                "file": filename.replace('\\', '/')
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server.status.name.lower()}, command discarded.")

    @staticmethod
    def format_campaigns(data, marker, marker_emoji):
        embed = discord.Embed(title="List of Campaigns", color=discord.Color.blue())
        ids = names = times = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            names += data[i]['name'] + '@' + data[i]['server_name'] + '\n'
            times += f"{data[i]['start']:%y-%m-%d} - " + (f"{data[i]['stop']:%y-%m-%d}" if data[i]['stop'] else '') + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Start/End', value=times)
        embed.set_footer(text='Press a number to display details about that specific campaign.')
        return embed

    @commands.command(description='Campaign Management',
                      usage='<add|start|stop|delete|list>',
                      aliases=['season', 'campaigns', 'seasons'])
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str], name: Optional[str], start_time: Optional[str], end_time: Optional[str]):
        server: Server = await self.bot.get_server(ctx)
        if not command:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                    cursor.execute(f"SELECT TRIM(regexp_replace(server_name, "
                                   f"'{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g')) AS server_name, name, "
                                   f"start, stop FROM campaigns WHERE NOW() BETWEEN start AND COALESCE(stop, NOW())")
                    if cursor.rowcount > 0:
                        report = Report(self.bot, self.plugin_name, 'campaign.json')
                        env = await report.render(campaign=dict(cursor.fetchone()), title='Active Campaign')
                        await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No running campaign found.')
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)
        elif command.lower() == 'add':
            if not name:
                await ctx.send(f"Usage: {self.bot.config['BOT']['COMMAND_PREFIX']}.campaign add <name> <start> [stop]")
                return
            if not start_time:
                await ctx.send(f"Usage: {self.bot.config['BOT']['COMMAND_PREFIX']}.campaign add <name> <start> [stop]")
                return
            description = await utils.input_value(self, ctx, 'Please enter a short description for this campaign '
                                                             '(. for none):')
            try:
                self.eventlistener.campaign('add', server, name, description,
                                            dateparser.parse(start_time, settings={'TIMEZONE': 'UTC'}) if start_time else None,
                                            dateparser.parse(end_time, settings={'TIMEZONE': 'UTC'}) if end_time else None)
                await ctx.send(f"Campaign {name} added on server {server.name}")
            except psycopg2.errors.ExclusionViolation:
                await ctx.send(f"A campaign is already configured for this timeframe on server {server.name}!")
            except psycopg2.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists on server {server.name}!")
        elif command.lower() == 'start':
            try:
                if not name:
                    await ctx.send(f"Usage: {self.bot.config['BOT']['COMMAND_PREFIX']}.campaign start <name>")
                    return
                self.eventlistener.campaign('start', server, name)
                await ctx.send(f"Campaign {name} started on server {server.name}")
            except psycopg2.errors.ExclusionViolation:
                await ctx.send(f"There is a campaign already running on server {server.name}!")
            except psycopg2.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists on server {server.name}!")
        elif command.lower() == 'stop':
            warn_text = f"Do you want to stop the running campaign on server \"{server.name}\"?"
            if await utils.yn_question(self, ctx, warn_text) is True:
                self.eventlistener.campaign('stop', server)
                await ctx.send(f"Campaign stopped.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() in ['del', 'delete']:
            if name:
                warn_text = f"Do you want to delete campaign \"{name}\" on server \"{server.name}\"?"
            else:
                warn_text = f"Do you want to delete the current running campaign on server \"{server.name}\"?"
            if await utils.yn_question(self, ctx, warn_text) is True:
                self.eventlistener.campaign('delete', server, name)
                await ctx.send(f"Campaign deleted.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() == 'list':
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                    if name != "-all":
                        cursor.execute(f"SELECT TRIM(regexp_replace(server_name, "
                                       f"'{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g')) AS server_name, "
                                       f"name, start, stop FROM campaigns WHERE COALESCE(stop, NOW()) >= NOW() ORDER "
                                       f"BY start DESC")
                    else:
                        cursor.execute(f"SELECT TRIM(regexp_replace(server_name, "
                                       f"'{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g')) AS server_name, "
                                       f"name, start, stop FROM campaigns ORDER BY start DESC")
                    if cursor.rowcount > 0:
                        campaigns = [dict(row) for row in cursor.fetchall()]
                        n = await utils.selection_list(self, ctx, campaigns, self.format_campaigns)
                        if n != -1:
                            report = Report(self.bot, self.plugin_name, 'campaign.json')
                            env = await report.render(campaign=campaigns[n], title='Campaign Overview')
                            await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No upcoming campaigns found.')
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)
        else:
            await ctx.send(f"Usage: {self.bot.config['BOT']['COMMAND_PREFIX']}.campaign <add|start|stop|delete|list>")


class GameMasterMaster(GameMasterAgent):

    @commands.command(description='Join a coalition (red / blue)', usage='[red | blue]')
    @utils.has_role('DCS')
    @utils.has_not_roles(['Coalition Blue', 'Coalition Red', 'GameMaster'])
    @commands.guild_only()
    async def join(self, ctx, coalition: str):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.bot.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.bot.config['ROLES']['Coalition Blue'])
        }
        if coalition.casefold() not in roles.keys():
            await ctx.send('Usage: {}join [{}]'.format(self.bot.config['BOT']['COMMAND_PREFIX'], '|'.join(roles.keys())))
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # we don't care about coalitions if they left longer than one day before
                cursor.execute("SELECT coalition FROM players WHERE discord_id = %s AND coalition_leave > (NOW() - "
                               "interval %s)", (member.id, self.bot.config['BOT']['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        await ctx.send(f"You can't join the {coalition} coalition in-between "
                                       f"{self.bot.config['BOT']['COALITION_LOCK_TIME']} of leaving a coalition.")
                        await self.bot.audit(f'tried to join a new coalition in-between the time limit.', user=member)
                        return
                await member.add_roles(roles[coalition.lower()])
                cursor.execute('UPDATE players SET coalition = %s WHERE discord_id = %s', (coalition, member.id))
                await ctx.send(f'Welcome to the {coalition} side!')
                conn.commit()
        except discord.Forbidden:
            await ctx.send("I can't add you to this coalition. Please contact an Admin.")
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Leave your current coalition')
    @utils.has_roles(['Coalition Blue', 'Coalition Red'])
    @commands.guild_only()
    async def leave(self, ctx):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.bot.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.bot.config['ROLES']['Coalition Blue'])
        }
        for key, value in roles.items():
            if value in member.roles:
                conn = self.bot.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('UPDATE players SET coalition = NULL, coalition_leave = NOW() WHERE discord_id '
                                       '= %s', (member.id,))
                    conn.commit()
                    await member.remove_roles(value)
                    await ctx.send(f"You've left the {key} coalition!")
                    return
                except discord.Forbidden:
                    await ctx.send("I can't remove you from this coalition. Please contact an Admin.")
                    await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
                except (Exception, psycopg2.DatabaseError) as error:
                    self.bot.log.exception(error)
                    conn.rollback()
                finally:
                    self.bot.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(GameMasterMaster(bot, GameMasterEventListener))
    else:
        bot.add_cog(GameMasterAgent(bot, GameMasterEventListener))
