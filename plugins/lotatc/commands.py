import aiofiles
import aiohttp
import discord
import json
import os

from core import Plugin, utils, Server, get_translation, Group, Coalition, Status, ServerUploadHandler
from discord import app_commands
from discord.ext import commands
from jsonschema import validate, ValidationError
from services.bot import DCSServerBot
from typing import Literal

from .listener import LotAtcEventListener

LOTATC_DIR = r"Mods\services\LotAtc\userdb\transponders\{}"

_ = get_translation(__name__.split('.')[1])


async def gci_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        if not server:
            return []
        coalition: str = interaction.namespace.coalition
        listener = interaction.client.cogs['LotAtc'].eventlistener
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x)
            for x in listener.on_station.get(server.name, {}).get(coalition, {}).keys()
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class LotAtc(Plugin[LotAtcEventListener]):

    @staticmethod
    def lotatc_server_filter(server: Server) -> bool:
        extensions = server.instance.locals.get('extensions')
        return 'LotAtc' in extensions if extensions is not None else False

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
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(Coalition(coalition), {})
        if not gcis:
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        elif gci not in gcis:
            await interaction.response.send_message(_("GCI {} not found.").format(gci), ephemeral=True)
            return
        await interaction.response.send_message(embed=self.eventlistener.create_gci_embed(gcis[gci]))

    @gci.command(name="list", description=_('List of GCIs'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    coalition: Literal['blue', 'red']):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(Coalition(coalition), {})
        if not gcis:
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("List of active {} GCIs").format(coalition)
        embed.description = _("Server: {}").format(server.display_name)
        embed.add_field(name="Name", value='\n'.join(gcis.keys()))
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        patterns = [r'\.json$']
        if not ServerUploadHandler.is_valid(message, patterns=patterns, roles=self.bot.roles['DCS Admin']):
            return
        try:
            server = await ServerUploadHandler.get_server(message, filter_func=self.lotatc_server_filter)
            if not server:
                await message.channel.send(_("LotAtc is not configured on any server."))
                return

            handler = ServerUploadHandler(server=server, message=message, patterns=patterns)
            async with aiofiles.open('plugins/lotatc/schemas/lotatc_schema.json', mode='r') as infile:
                schema = json.loads(await infile.read())

            for attachment in message.attachments:
                async with aiohttp.ClientSession() as session:
                    async with session.get(message.attachments[0].url, proxy=self.node.proxy,
                                           proxy_auth=self.node.proxy_auth) as response:
                        if response.status == 200:
                            data = await response.json(encoding="utf-8")
                            try:
                                validate(instance=data, schema=schema)
                            except ValidationError:
                                await message.channel.send(f"Could not upload file {attachment.filename} "
                                                           f"as it is no valid transponder file.")
                                continue

                root = server.instance.home
                base_dir = os.path.join(root, LOTATC_DIR.format("blue") if "blue" in attachment.filename else "red")
                await handler.upload(base_dir)
        except Exception as ex:
            self.log.exception(ex)
            await message.channel.send("Error while uploading. Check the DCSServerBot log.")
        finally:
            await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(LotAtc(bot, LotAtcEventListener))
