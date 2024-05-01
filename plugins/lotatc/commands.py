import aiohttp
import discord
import json
import os

from core import Plugin, utils, Server, get_translation, Group, Coalition, Status
from discord import app_commands
from discord.ext import commands
from jsonschema import validate, ValidationError
from services import DCSServerBot
from typing import Optional, Literal

from .listener import LotAtcEventListener

LOTATC_DIR = r"Mods\services\LotAtc\userdb\transponders\{}"

_ = get_translation(__name__.split('.')[1])


async def gci_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        coalition: str = utils.get_interaction_param(interaction, 'coalition')
        listener = interaction.client.cogs['LotAtc'].eventlistener
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x)
            for x in listener.on_station.get(server.name, {}).get(coalition, {}).keys()
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class LotAtc(Plugin):

    def lotatc_server_filter(self, server: Server) -> bool:
        extensions = server.instance.locals.get('extensions')
        return 'LotAtc' in extensions if extensions is not None else False

    async def get_server(self, message: discord.Message) -> Optional[Server]:
        server: Server = self.bot.get_server(message, admin_only=True)
        if server:
            return server
        ctx = await self.bot.get_context(message)
        # check if we are in the correct channel
        if self.bot.locals.get('admin_channel', 0) != message.channel.id:
            return None
        try:
            return await utils.server_selection(
                self.bus, ctx, title=_("To which server do you want to upload this transponder file to?"),
                filter_func=self.lotatc_server_filter)
        except Exception as ex:
            self.log.exception(ex)
            return None

    # New command group "/gci"
    gci = Group(name="gci", description=_("Commands to manage GCIs"))

    @gci.command(description=_('Info about a GCI'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(gci=gci_autocomplete)
    async def info(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   coalition: Literal['blue', 'red'], gci: str):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(coalition, {})
        if not gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        elif gci not in gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("GCI {} not found.").format(gci), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=self.eventlistener.create_gci_embed(gcis[gci]))

    @gci.command(name="list", description=_('List of GCIs'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    coalition: Literal['blue', 'red']):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(coalition, {})
        if not gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("List of active {} GCIs").format(coalition)
        embed.description = _("Server: {}").format(server.display_name)
        embed.add_field(name="Name", value='\n'.join(gcis.keys()))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages
        if message.author.bot:
            return
        if not message.attachments or not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        for attachment in message.attachments:
            if not attachment.filename.endswith('.json'):
                continue
            async with aiohttp.ClientSession() as session:
                async with session.get(message.attachments[0].url) as response:
                    if response.status == 200:
                        data = await response.json(encoding="utf-8")
                        with open('plugins/lotatc/schemas/lotatc_schema.json', mode='r') as infile:
                            schema = json.load(infile)
                        try:
                            validate(instance=data, schema=schema)
                        except ValidationError:
                            return
            # We have a proper LotAtc transponder json
            try:
                server = await self.get_server(message)
                if not server:
                    await message.channel.send(_("LotAtc is not configured on any server."))
                    return

                root = server.instance.home
                filename = os.path.join(root, LOTATC_DIR.format("blue") if "blue" in attachment.filename else "red",
                                        attachment.filename)
                await server.node.write_file(filename, attachment.url, overwrite=True)
                await message.channel.send(_('Transponder file {} uploaded.').format(attachment.filename))
            except Exception as ex:
                self.log.exception(ex)
                await message.channel.send(_('Transponder file {} could not be uploaded!').format(attachment.filename))
            finally:
                await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(LotAtc(bot, LotAtcEventListener))
