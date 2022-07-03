import discord
import psycopg2
from contextlib import closing
from copy import deepcopy
from core import utils, DCSServerBot, Plugin, PluginRequiredError, Server
from discord.ext import commands
from typing import Optional
from .listener import CreditSystemListener


class CreditSystemAgent(Plugin):

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = {}
                    if 'points_per_kill' in default and 'points_per_kill' not in specific:
                        merged['points_per_kill'] = default['points_per_kill']
                    elif 'points_per_kill' not in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = specific['points_per_kill']
                    elif 'points_per_kill' in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = default['points_per_kill'] + specific['points_per_kill']
                    self._config[server.name] = merged
                    server.sendtoDCS({
                        'command': 'loadParams',
                        'plugin': self.plugin_name,
                        'params': self._config[server.name]
                    })
            else:
                return None
        return self._config[server.name] if server.name in self._config else None


class CreditSystemMaster(CreditSystemAgent):

    @commands.command(description='Displays your current credits')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def credits(self, ctx):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute(f"SELECT trim(regexp_replace(c.server_name, '"
                               f"{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g')), p.name, COALESCE(SUM("
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
                timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
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
        bot.add_cog(CreditSystemMaster(bot, CreditSystemListener))
    else:
        bot.add_cog(CreditSystemAgent(bot, CreditSystemListener))
