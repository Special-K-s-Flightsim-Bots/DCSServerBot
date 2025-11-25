import discord

from core import Plugin, utils, Channel, Coalition, Server, get_translation
from discord import app_commands
from discord.app_commands import Group
from services.bot import DCSServerBot

_ = get_translation(__name__.split('.')[1])


class Battleground(Plugin):

    battleground = Group(name="battleground", description=_("DCSBattleground commands"))

    @battleground.command(description=_('Push MGRS coordinates with screenshots to DCS Battleground'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def recon(self, interaction: discord.Interaction, name: str, mgrs: str, screenshot: discord.Attachment):
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
            screenshots = [att.url for att in [screenshot]]  # TODO: add multiple ones
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO bg_geometry(id, type, name, posmgrs, screenshot, discordname, avatar, side, server) 
                        VALUES (nextval('bg_geometry_id_seq'), 'recon', %s, %s, %s, %s, %s, %s, %s)
                    """, (name, mgrs, screenshots, interaction.user.name, interaction.user.display_avatar.url,
                          side, server.name))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Recon data added - {side} side - {server}").format(side=side, server=server.name),
                delete_after=self.bot.locals.get('message_autodelete')
            )
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
            async with conn.transation():
                await conn.execute("DELETE FROM bg_geometry WHERE server = %s", (server.name, ))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Recon data deleted for server {}.").format(server.name),
                                                ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Battleground(bot))
