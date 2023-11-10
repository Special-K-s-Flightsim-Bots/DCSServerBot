import discord
import psycopg
from discord import app_commands
from discord.app_commands import Group

from core import Plugin, utils, Channel, Coalition, Server
from services import DCSServerBot


class Battleground(Plugin):

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str) -> None:
        conn.execute("UPDATE bg_geometry SET server = %s WHERE server= %s", (new_name, old_name))

    battleground = Group(name="battleground", description="DCSBattleground commands")

    @battleground.command(description='Push MGRS coordinates with screenshots to DCS Battleground')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def recon(self, interaction: discord.Interaction, name: str, mgrs: str, screenshot: discord.Attachment):
        mgrs = mgrs.replace(' ', '')
        if len(mgrs) != 15 or not mgrs[:2].isnumeric() or not mgrs[5:].isnumeric():
            await interaction.response.send_message('The second parameter needs to be a MGRS coordinate '
                                                    '(ex: 38TLN0274366889)', ephemeral=True)
            return
        done = False
        for server in self.bot.servers.values():
            sides = utils.get_sides(interaction.client, interaction, server)
            blue_channel = server.channels.get(Channel.COALITION_BLUE_CHAT)
            red_channel = server.channels.get(Channel.COALITION_RED_CHAT)
            if Coalition.BLUE in sides and blue_channel and blue_channel.id == interaction.message.channel.id:
                side = "blue"
            elif Coalition.RED in sides and red_channel and red_channel.id == interaction.message.channel.id:
                side = "red"
            else:
                continue
            done = True
            screenshots = [att.url for att in [screenshot]]  # TODO: add multiple ones
            with self.pool.connection() as conn:
                with conn.transation():
                    conn.execute("""
                        INSERT INTO bg_geometry(id, type, name, posmgrs, screenshot, discordname, avatar, side, server) 
                        VALUES (nextval('bg_geometry_id_seq'), 'recon', %s, %s, %s, %s, %s, %s, %s)
                    """, (name, mgrs, screenshots, interaction.user.name, interaction.user.display_avatar.url,
                          side, server.name))
            await interaction.response.send_message(f"Recon data added - {side} side - {server.name}")
        if not done:
            await interaction.response.send_message('Coalitions have to be enabled and you need to use this command '
                                                    'in one of your coalition channels.', ephemeral=True)

    @battleground.command(description='Delete recon data on a specified server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        with self.pool.connection() as conn:
            with conn.transation():
                conn.execute("DELETE FROM bg_geometry WHERE server = %s", (server.name, ))
        await interaction.response.send_message(f"Recon data deleted for server {server.name}", ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Battleground(bot))
