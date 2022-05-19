import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, utils
from core.const import Status
from discord.ext import commands
from .listener import GameMasterEventListener


class GameMasterAgent(Plugin):

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages
        if message.author.bot:
            return
        for server in self.globals.values():
            if server['status'] != Status.RUNNING:
                continue
            if self.config[server['installation']]['COALITIONS']:
                sides = utils.get_sides(message, server)
                if 'Blue' in sides and 'coalition_blue_channel' in server and \
                        server["coalition_blue_channel"] == str(message.channel.id):
                    # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                    # self.bot.sendtoDCS(server, {
                    #    "command": "sendChatMessage",
                    #    "message": message.content,
                    #    "from": message.author.display_name,
                    #    "to": const.SIDE_BLUE * -1
                    # })
                    pass
                elif 'Red' in sides and 'coalition_red_channel' in server and \
                        server["coalition_red_channel"] == str(message.channel.id):
                    # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                    # self.bot.sendtoDCS(server, {
                    #    "command": "sendChatMessage",
                    #    "message": message.content,
                    #    "from": message.author.display_name,
                    #    "to": const.SIDE_BLUE * -1
                    # })
                    pass
            if 'chat_channel' in server and server["chat_channel"] == str(message.channel.id):
                if message.content.startswith(self.config['BOT']['COMMAND_PREFIX']) is False:
                    self.bot.sendtoDCS(server, {
                        "command": "sendChatMessage",
                        "message": message.content,
                        "from": message.author.display_name
                    })

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @utils.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server and server['status'] == Status.RUNNING:
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "channel": ctx.channel.id,
                "message": ' '.join(args),
                "from": ctx.message.author.display_name
            })

    @commands.command(description='Sends a popup to a coalition', usage='<coal.|user> [time] <msg>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] != Status.RUNNING:
                await ctx.send(f"Mission is {server['status'].name.lower()}, message discarded.")
                return
            if len(args) > 0:
                if args[0].isnumeric():
                    time = int(args[0])
                    i = 1
                else:
                    time = self.config['BOT']['MESSAGE_TIMEOUT']
                    i = 0
                if to not in ['all', 'red', 'blue']:
                    player = utils.get_player(self, server['server_name'], name=to)
                    if player and 'slot' in player and len(player['slot']) > 0:
                        to = player['slot']
                    else:
                        await ctx.send(f"Can't find player {to} or player is not in an active unit.")
                        return
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "channel": ctx.channel.id,
                    "message": ' '.join(args[i:]),
                    "time": time,
                    "from": ctx.message.author.display_name,
                    "to": to.lower()
                })
                await ctx.send('Message sent.')
            else:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue|user [time] <message>")

    @commands.command(description='Set or clear a flag inside the mission environment', usage='<flag> [value]')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def flag(self, ctx, flag: int, value: int = None):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            if value:
                self.bot.sendtoDCS(server, {
                    "command": "setFlag",
                    "channel": ctx.channel.id,
                    "flag": flag,
                    "value": value
                })
                await ctx.send(f"Flag {flag} set to {value}.")
            else:
                data = await self.bot.sendtoDCSSync(server, {"command": "getFlag", "flag": flag})
                await ctx.send(f"Flag {flag} has value {data['value']}.")
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, can't set/get flag.")

    @commands.command(description='Calls any function inside the mission environment', usage='<script>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script(self, ctx, *script):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script",
                "script": ' '.join(script)
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")

    @commands.command(description='Loads a lua file into the mission environment', usage='<file>')
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def do_script_file(self, ctx, filename):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script_file",
                "file": filename.replace('\\', '/')
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")


class GameMasterMaster(GameMasterAgent):

    @commands.command(description='Join a coalition (red / blue)', usage='[red | blue]')
    @utils.has_role('DCS')
    @utils.has_not_roles(['Coalition Blue', 'Coalition Red', 'GameMaster'])
    @commands.guild_only()
    async def join(self, ctx, coalition: str):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
        }
        if coalition.casefold() not in roles.keys():
            await ctx.send('Usage: {}join [{}]'.format(self.config['BOT']['COMMAND_PREFIX'], '|'.join(roles.keys())))
            return
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # we don't care about coalitions if they left longer than one day before
                cursor.execute("SELECT coalition FROM players WHERE discord_id = %s AND coalition_leave > (NOW() - "
                               "interval %s)", (member.id, self.config['BOT']['COALITION_LOCK_TIME']))
                if cursor.rowcount == 1:
                    if cursor.fetchone()[0] != coalition.casefold():
                        await ctx.send(f"You can't join the {coalition} coalition in-between "
                                       f"{self.config['BOT']['COALITION_LOCK_TIME']} of leaving a coalition.")
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
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
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
