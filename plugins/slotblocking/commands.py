import discord
import psycopg2
from contextlib import closing
from copy import deepcopy
from core import DCSServerBot, Plugin, utils, PluginRequiredError
from discord.ext import commands
from typing import Optional
from .listener import SlotBlockingListener


class SlotBlockingAgent(Plugin):

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

    @commands.command(description='Campaign management', usage='[start / stop / reset]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str]):
        server = await utils.get_server(self, ctx)
        if server:
            if not command:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}campaign [start / stop / reset]")
            elif command.lower() == 'start':
                self.eventlistener.campaign('start', server)
                await ctx.send(f"Campaign started for server {server['server_name']}")
            elif command.lower() == 'stop':
                if await self.campaign(ctx, 'reset'):
                    self.eventlistener.campaign('stop', server)
                    await ctx.send(f"Campaign stopped for server {server['server_name']}")
            elif command.lower() == 'reset':
                if await utils.yn_question(self, ctx, 'Do you want to delete the old campaign data for server '
                                                      '"{}"?'.format(server['server_name'])) is True:
                    self.eventlistener.campaign('reset', server)
                    await ctx.send(f"Old campaign data wiped for server {server['server_name']}")
                    return True
                else:
                    await ctx.send('Aborted.')
                    return False

    def get_config(self, server: dict) -> Optional[dict]:
        if self.plugin_name not in server:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server['installation'] == element['installation']) or \
                                ('server_name' in element and server['server_name'] == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    server[self.plugin_name] = default
                elif specific and not default:
                    server[self.plugin_name] = specific
                elif default and specific:
                    merged = {}
                    if 'use_reservations' in specific:
                        merged['use_reservations'] = specific['use_reservations']
                    elif 'use_reservations' in default:
                        merged['use_reservations'] = default['use_reservations']
                    if 'restricted' in default and 'restricted' not in specific:
                        merged['restricted'] = default['restricted']
                    elif 'restricted' not in default and 'restricted' in specific:
                        merged['restricted'] = specific['restricted']
                    elif 'restricted' in default and 'restricted' in specific:
                        merged['restricted'] = default['restricted'] + specific['restricted']
                    if 'points_per_kill' in default and 'points_per_kill' not in specific:
                        merged['points_per_kill'] = default['points_per_kill']
                    elif 'points_per_kill' not in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = specific['points_per_kill']
                    elif 'points_per_kill' in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = default['points_per_kill'] + specific['points_per_kill']
                    server[self.plugin_name] = merged
                    self.bot.sendtoDCS(server, {'command': 'loadParams',
                                                'plugin': self.plugin_name,
                                                'params': server[self.plugin_name]})
            else:
                return None
        return server[self.plugin_name] if self.plugin_name in server else None


class SlotBlockingMaster(SlotBlockingAgent):

    @commands.command(description='Displays your current credits')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def credits(self, ctx):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute(f"SELECT trim(regexp_replace(c.server_name, '"
                               f"{self.config['FILTER']['SERVER_FILTER']}', '', 'g')), p.name, COALESCE(SUM("
                               f"s.points), 0) AS credits FROM sb_points s, players p, campaigns c WHERE "
                               f"s.player_ucid = p.ucid AND p.discord_id = %s AND s.campaign_id = c.campaign_id GROUP "
                               f"BY 1, 2", (ctx.message.author.id, ))
                if cursor.rowcount == 0:
                    await ctx.send('You currently have 0 campaign credits.')
                    return
                embed = discord.Embed(title='Campaign Credits', color=discord.Color.blue())
                embed.description = 'You currently have these credits:'
                servers = names = credits = ''
                for row in cursor.fetchall():
                    servers += row[0] + '\n'
                    names += row[1] + '\n'
                    credits += f"{row[2]:.2f}\n"
                embed.add_field(name='Server', value=servers)
                embed.add_field(name='DCS Name', value=names)
                embed.add_field(name='Points', value=credits)
                timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
                await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(SlotBlockingMaster(bot, SlotBlockingListener))
    else:
        bot.add_cog(SlotBlockingAgent(bot, SlotBlockingListener))
