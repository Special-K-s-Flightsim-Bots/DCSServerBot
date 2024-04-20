import aiohttp
import discord
import json
import os

from core import Plugin, utils, Server, get_translation
from discord.ext import commands
from jsonschema import validate, ValidationError
from services import DCSServerBot
from typing import Optional

LOTATC_DIR = r"Mods\services\LotAtc\userdb\transponders\{}"

_ = get_translation(__name__.split('.')[1])


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
    await bot.add_cog(LotAtc(bot))
