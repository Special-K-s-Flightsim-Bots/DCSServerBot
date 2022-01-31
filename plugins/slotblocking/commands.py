import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, utils, PluginRequiredError
from discord.ext import commands
from typing import Optional
from .listener import SlotBlockingListener


class SlotBlocking(Plugin):

    @commands.command(description='Campaign management', usage='[start / stop / reset]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str]):
        server = await utils.get_server(self, ctx)
        if server:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    if not command:
                        await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}campaign [start / stop / reset]")
                    elif command.lower() == 'start':
                        if await self.campaign(ctx, 'reset'):
                            cursor.execute('INSERT INTO campaigns (server_name) VALUES (%s)', (server['server_name'],))
                            await ctx.send(f"Campaign started for server {server['server_name']}")
                    elif command.lower() == 'stop':
                        if await self.campaign(ctx, 'reset'):
                            cursor.execute('DELETE FROM campaigns WHERE server_name = %s', (server['server_name'],))
                            await ctx.send(f"Campaign stopped for server {server['server_name']}")
                    elif command.lower() == 'reset':
                        if await utils.yn_question(self, ctx, 'Do you want to delete the old campaign data for server '
                                                              '"{}"?'.format(server['server_name'])) is True:
                            cursor.execute('DELETE FROM sb_points WHERE campaign_id = (SELECT campaign_id FROM '
                                           'campaigns WHERE server_name = %s)', (server['server_name'],))
                            await ctx.send(f"Old campaign data wiped for server {server['server_name']}")
                            return True
                        else:
                            await ctx.send('Aborted.')
                            return False
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(SlotBlocking(bot, SlotBlockingListener(bot)))
