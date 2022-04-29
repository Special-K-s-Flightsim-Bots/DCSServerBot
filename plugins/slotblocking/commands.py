import discord
import psycopg2
from contextlib import closing
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
