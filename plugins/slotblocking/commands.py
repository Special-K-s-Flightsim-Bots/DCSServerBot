import discord
import psycopg2
from contextlib import closing
from copy import deepcopy
from core import DCSServerBot, Plugin, utils, PluginRequiredError
from discord.ext import commands
from typing import Optional
from .listener import SlotBlockingListener


class SlotBlockingAgent(Plugin):

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
                               f"s.points), 0) AS credits FROM credits s, players p, campaigns c WHERE "
                               f"s.player_ucid = p.ucid AND p.discord_id = %s AND s.campaign_id = c.id AND "
                               f"NOW() BETWEEN c.start AND COALESCE(c.stop, NOW()) GROUP BY 1, 2",
                               (ctx.message.author.id, ))
                if cursor.rowcount == 0:
                    await ctx.send('You currently have 0 campaign credits.')
                    return
                embed = discord.Embed(title='Campaign Credits', color=discord.Color.blue())
                embed.description = 'You currently have these credits:'
                servers = names = points = ''
                for row in cursor.fetchall():
                    servers += row[0] + '\n'
                    names += row[1] + '\n'
                    points += f"{row[2]:.2f}\n"
                embed.add_field(name='Server', value=servers)
                embed.add_field(name='DCS Name', value=names)
                embed.add_field(name='Points', value=points)
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
