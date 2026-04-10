import aiohttp
import discord
import json
import os

from core import utils, get_translation, Server, ServerUploadHandler
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validate
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import GameMaster

_ = get_translation(__name__.split('.')[1])


class GameMasterUploadHandler(ServerUploadHandler):

    def __init__(self, plugin: "GameMaster", server: Server, message: discord.Message, patterns: list[str]):
        super().__init__(server, message, patterns)
        self.plugin = plugin
        self.log = plugin.log

    async def create_embed(self, att: discord.Attachment) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(att.url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                if response.status != 200:
                    await self.channel.send(_('Error {} while reading JSON file!').format(response.status))
                    return
                data = await response.json(encoding="utf-8")

        with open(os.path.join('plugins', self.plugin.plugin_name, 'schemas', 'embed_schema.json'), mode='r') as infile:
            schema = json.load(infile)
        try:
            validate(instance=data, schema=schema)
        except ValidationError:
            return
        embed = utils.format_embed(data, server=self.server, user=self.message.author)
        msg = None
        if 'message_id' in data:
            try:
                msg = await self.channel.fetch_message(int(data['message_id']))
                await msg.edit(embed=embed)
            except discord.errors.NotFound:
                msg = None
            except discord.errors.DiscordException as ex:
                self.log.exception(ex)
                await self.channel.send(_('Error while updating embed!'))
                return
        if not msg:
            await self.channel.send(embed=embed)
        await self.message.delete()

    async def upload(self, base_dir: str, ignore_list: list[str] | None = None):
        for att in self.message.attachments:
            if att.filename.endswith('.lua'):
                await super().upload(base_dir, ignore_list)
            elif att.filename.endswith('.json'):
                await self.create_embed(att)

    async def post_upload(self, uploaded: list[discord.Attachment]):
        num = len(uploaded)
        if num > 0:
            await self.channel.send(
                _("{num} LUA files uploaded. You can load any of them with {command} now.").format(
                    num=num, command=(await utils.get_command(self.bot, name=self.plugin.do_script_file.name)).mention
                )
            )
