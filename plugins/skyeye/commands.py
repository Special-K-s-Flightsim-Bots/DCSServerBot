import json
import os

import aiofiles
import aiohttp
import discord
from jsonschema import validate, ValidationError

from core import Plugin, ServerUploadHandler, Server, get_translation
from discord.ext import commands

_ = get_translation(__name__.split('.')[1])


class SkyEye(Plugin):

    @staticmethod
    def skyeye_server_filter(server: Server) -> bool:
        extensions = server.instance.locals.get('extensions')
        return 'SkyEye' in extensions if extensions is not None else False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        patterns = [r'^locations.json$']
        if not ServerUploadHandler.is_valid(message, patterns=patterns, roles=self.bot.roles['DCS Admin']):
            return

        # check schema
        attachments = []
        async with aiofiles.open('plugins/skyeye/schemas/skyeye_schema.json', mode='r') as infile:
            schema = json.loads(await infile.read())
        for attachment in message.attachments:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url, proxy=self.node.proxy,
                                       proxy_auth=self.node.proxy_auth) as response:
                    if response.status == 200:
                        data = await response.json(encoding="utf-8")
                        try:
                            validate(instance=data, schema=schema)
                            attachments.append(attachment)
                        except ValidationError:
                            continue

        # no valid attachment found
        if not attachments:
            return

        try:
            server = await ServerUploadHandler.get_server(message, filter_func=self.skyeye_server_filter)
            if not server:
                await message.channel.send(_("SkyEye is not configured on any server."))
                return

            handler = ServerUploadHandler(server=server, message=message, patterns=patterns)
            base_dir = os.path.join(server.instance.home, 'Config')
            await handler.upload(base_dir, attachments=attachments)
        except Exception as ex:
            self.log.exception(ex)
            await message.channel.send("Error while uploading. Check the DCSServerBot log.")
        finally:
            await message.delete()


async def setup(bot):
    await bot.add_cog(SkyEye(bot))
