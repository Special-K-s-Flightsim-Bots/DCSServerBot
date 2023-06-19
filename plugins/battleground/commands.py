import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, utils, Channel, Coalition
from discord.ext import commands


class Battleground(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("UPDATE bg_geometry SET server = %s WHERE server= %s", (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.debug(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Push MGRS coordinates with screenshots to DCS Battleground')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def recon(self, ctx: commands.Context, name: str, mgrs: str):
        if not ctx.message.attachments or not ctx.message.attachments[0].filename[-4:] in ['.jpg', '.gif', '.png']:
            await ctx.send('You need to add one or more screenshots (.jpg/.gif/.png)')
            return
        if len(mgrs) != 15 or not mgrs[:2].isnumeric() or not mgrs[5:].isnumeric():
            await ctx.send('The second parameter need to be a MGRS coordinate (ex: 38TLN0274366889)')
            return
        done = False
        for server in self.bot.servers.values():
            sides = utils.get_sides(ctx.message, server)
            blue_channel = server.get_channel(Channel.COALITION_BLUE_CHAT)
            red_channel = server.get_channel(Channel.COALITION_RED_CHAT)
            if Coalition.BLUE in sides and blue_channel and blue_channel.id == ctx.message.channel.id:
                side = "blue"
            elif Coalition.RED in sides and red_channel and red_channel.id == ctx.message.channel.id:
                side = "red"
            else:
                continue

            done = True
            screenshots = [att.url for att in ctx.message.attachments]
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute("""
                        INSERT INTO bg_geometry(id, type, name, posmgrs, screenshot, discordname, avatar, side, server) 
                        VALUES (nextval('bg_geometry_id_seq'), 'recon', %s, %s, %s, %s, %s, %s, %s)
                    """, (name, mgrs, screenshots, ctx.message.author.name, ctx.message.author.display_avatar.url,
                          side, server.name))
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            await ctx.send("Recon data added - " + side + " side - " + server.name)
        if not done:
            await ctx.send('Coalitions have to be enabled and you need to use this command in one of your '
                           'coalition channels.')


async def setup(bot: DCSServerBot):
    await bot.add_cog(Battleground(bot))
