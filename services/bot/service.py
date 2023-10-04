from __future__ import annotations

import aiofiles
import aiohttp
import discord
import os
import shutil
import zipfile

from core import ServiceRegistry, Service, utils
from discord.ext import commands
from discord.utils import MISSING
from io import BytesIO
from matplotlib import font_manager
from typing import Optional, Union, TYPE_CHECKING

from .dcsserverbot import DCSServerBot

if TYPE_CHECKING:
    from core import Server, Plugin

__all__ = ["BotService"]


@ServiceRegistry.register("Bot", master_only=True)
class BotService(Service):

    def __init__(self, node, name: str):
        super().__init__(node=node, name=name)
        self.bot = None

    def init_bot(self):
        def get_prefix(client, message):
            prefixes = [self.locals.get('command_prefix', '.')]
            # Allow users to @mention the bot instead of using a prefix
            return commands.when_mentioned_or(*prefixes)(client, message)

        # Create the Bot
        return DCSServerBot(version=self.node.bot_version,
                            sub_version=self.node.sub_version,
                            command_prefix=get_prefix,
                            description='Interact with DCS World servers',
                            owner_id=self.locals['owner'],
                            case_insensitive=True,
                            intents=discord.Intents.all(),
                            node=self.node,
                            locals=self.locals,
                            help_command=None,
                            heartbeat_timeout=120,
                            assume_unsync_clock=True)

    async def start(self, *, reconnect: bool = True) -> None:
        self.bot = self.init_bot()
        await self.install_fonts()
        await super().start()
        # cleanup the intercom channels
#        with self.pool.connection() as conn:
#            with conn.transaction():
#                conn.execute("DELETE FROM intercom WHERE node = %s", (self.node.name, ))
#                if self.node.master:
#                    conn.execute("DELETE FROM intercom WHERE node = 'Master'")
        async with self.bot:
            await self.bot.start(self.locals['token'], reconnect=reconnect)

    async def stop(self):
        if self.bot:
            await self.bot.close()
        await super().stop()

    async def alert(self, server: Server, message: str):
        mentions = ''
        for role_name in self.bot.roles['DCS Admin']:
            role: discord.Role = discord.utils.get(self.bot.guilds[0].roles, name=role_name)
            if role:
                mentions += role.mention
        message = mentions + ' ' + utils.escape_string(message)
        await self.bot.get_admin_channel(server).send(message)

    async def install_fonts(self):
        font = self.locals.get('reports', {}).get('cjk_font')
        if font:
            if not os.path.exists('fonts'):
                os.makedirs('fonts')

                async def fetch_file(url: str):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as resp:
                            assert resp.status == 200
                            data = await resp.read()

                    async with aiofiles.open(
                            os.path.join('fonts', "temp.zip"), "wb") as outfile:
                        await outfile.write(data)

                    with zipfile.ZipFile('fonts/temp.zip', 'r') as zip_ref:
                        for file in zip_ref.namelist():
                            if not file.endswith('.ttf') and not file.endswith('.otf'):
                                continue
                            zip_ref.extract(file, 'fonts')
                            if file != os.path.basename(file):
                                shutil.move(os.path.join('fonts', file), 'fonts')
                                os.rmdir(os.path.join('fonts', os.path.dirname(file)))

                    os.remove('fonts/temp.zip')
                    for f in font_manager.findSystemFonts('fonts'):
                        font_manager.fontManager.addfont(f)
                    self.log.info('- CJK font installed and loaded.')

                fonts = {
                    "TC": "https://fonts.google.com/download?family=Noto%20Sans%20TC",
                    "JP": "https://fonts.google.com/download?family=Noto%20Sans%20JP",
                    "KR": "https://fonts.google.com/download?family=Noto%20Sans%20KR"
                }

                await fetch_file(fonts[font])
            else:
                for f in font_manager.findSystemFonts('fonts'):
                    font_manager.fontManager.addfont(f)
                self.log.debug('- CJK fonts loaded.')

    async def send_message(self, channel: int, content: Optional[str] = None, server: Optional[Server] = None,
                           filename: Optional[str] = None, embed: Optional[dict] = None):
        _channel = self.bot.get_channel(channel)
        if embed:
            _embed = discord.Embed.from_dict(embed)
        else:
            _embed = MISSING
        if filename:
            data = await server.node.read_file(filename)
            file = discord.File(BytesIO(data), filename=os.path.basename(filename))
        else:
            file = MISSING
        await _channel.send(content=content, file=file, embed=_embed)

    async def send_dm(self, member: Optional[discord.Member] = None, content: Optional[str] = None,
                      server: Optional[Server] = None, filename: Optional[str] = None, embed: Optional[dict] = None):
        if member:
            channel = member.dm_channel.id
        else:
            channel = self.bot.guilds[0].get_member(self.bot.owner_id).dm_channel.id
        await self.send_message(channel, content=content, server=server, filename=filename, embed=embed)

    async def audit(self, message, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None):
        await self.bot.audit(message, user=user, server=server)

    def rename(self, server: Server, new_name: str):
        with self.pool.connection() as conn:
            with conn.transaction():
                # call rename() in all Plugins
                for plugin in self.bot.cogs.values():  # type: Plugin
                    plugin.rename(conn, server.name, new_name)
                conn.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                             (new_name, server.name))
                conn.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                             (new_name, server.name))
