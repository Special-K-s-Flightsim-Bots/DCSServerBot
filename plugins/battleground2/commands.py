import discord
import psycopg
import json

from core import Plugin, utils, Channel, Coalition, Server, get_translation
from discord import app_commands
from discord.app_commands import Group
from services.bot import DCSServerBot

from .listener import BattlegroundEventListener

_ = get_translation(__name__.split('.')[1])


class Battleground(Plugin[BattlegroundEventListener]):

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str) -> None:
        await conn.execute("UPDATE bg_geometry2 SET server_name = %s WHERE server_name = %s", (new_name, old_name))
        await conn.execute("UPDATE bg_missions SET server_name = %s WHERE server_name = %s", (new_name, old_name))
        await conn.execute("UPDATE bg_task SET server_name = %s WHERE server_name = %s", (new_name, old_name))

    battleground = Group(name="battleground", description=_("DCSBattleground commands"))

    @battleground.command(description=_('Push MGRS coordinates with screenshots to DCS Battleground'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def recon(self, interaction: discord.Interaction, name: str, mgrs: str, screenshot1: discord.Attachment,
                    screenshot2: discord.Attachment | None, screenshot3: discord.Attachment | None,
                    screenshot4: discord.Attachment | None, screenshot5: discord.Attachment | None,
                    screenshot6: discord.Attachment | None, screenshot7: discord.Attachment | None,
                    screenshot8: discord.Attachment | None, screenshot9: discord.Attachment | None,
                    screenshot10:discord.Attachment | None):
        mgrs = mgrs.replace(' ', '')
        if len(mgrs) != 15 or not mgrs[:2].isnumeric() or not mgrs[5:].isnumeric():
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('The second parameter needs to be a MGRS coordinate (ex: 38TLN0274366889).'), ephemeral=True)
            return
        done = False
        for server in self.bot.servers.values():
            sides = utils.get_sides(interaction.client, interaction, server)
            blue_channel = server.channels.get(Channel.COALITION_BLUE_CHAT)
            red_channel = server.channels.get(Channel.COALITION_RED_CHAT)
            if Coalition.BLUE in sides and blue_channel and blue_channel == interaction.channel_id:
                side = "blue"
            elif Coalition.RED in sides and red_channel and red_channel == interaction.channel_id:
                side = "red"
            else:
                continue
            done = True
            screenshots = [att.url for att in list(filter(lambda item: item is not None,[
                screenshot1,screenshot2,screenshot3,screenshot4,screenshot5,screenshot6,screenshot7,screenshot8,
                screenshot9,screenshot10
            ]))]
            author = {
                "name": interaction.user.name,
                "icon_url": interaction.user.display_avatar.url
            }
            fields = {
                'posPoint': [],
                'posMGRS': mgrs,
                'position_type': "MGRS",
                'screenshot': screenshots,
                'description': [],
                'side': side,
                'color': "#e4e70a",
                'status': "Shared",
                'type': "recon",
                'clickable': True,
                'points': [],
                'center': [],
                'radius': 0
            }
            data = {
                "title": name,
                "author": author,
                "fields": fields
            }
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("INSERT INTO bg_geometry2(server_name, data) VALUES (%s, %s)",
                                       (server.name, json.dumps(data)))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Recon data added - {side} side - {server}").format(side=side, server=server.name))
        if not done:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('Coalitions have to be enabled and you need to use this command in one of your coalition channels!'),
                ephemeral=True)

    @battleground.command(description=_('Delete recon data on a specified server'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM bg_geometry2 WHERE server_name = %s", (server.name, ))
                await conn.execute("DELETE FROM bg_missions WHERE server_name = %s", (server.name,))
                await conn.execute("DELETE FROM bg_task WHERE server_name = %s", (server.name,))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Recon data deleted for server {}.").format(server.name),
                                                ephemeral=utils.get_ephemeral(interaction))


async def setup(bot: DCSServerBot):
    await bot.add_cog(Battleground(bot, BattlegroundEventListener))
