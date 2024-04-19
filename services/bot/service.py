from __future__ import annotations

import aiofiles
import aiohttp
import asyncio
import discord
import os
import shutil
import zipfile

from core import utils
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import commands
from discord.utils import MISSING
from io import BytesIO
from matplotlib import font_manager
from typing import Optional, Union, TYPE_CHECKING

from .dcsserverbot import DCSServerBot

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from core import Server, Plugin

__all__ = ["BotService"]


@ServiceRegistry.register(master_only=True)
class BotService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Bot")
        self.bot: Optional[DCSServerBot] = None
        # do we need to change the bot.yaml file?
        if isinstance(self.locals.get('autorole'), str):
            value = self.locals.pop('autorole')
            if self.locals.get('roles', {}).get('DCS', []) and self.locals['roles']['DCS'][0] != '@everyone':
                if value == 'join':
                    self.locals['autorole'] = {
                        "on_join": self.locals['roles']['DCS'][0]
                    }
                elif value == 'linkme':
                    self.locals['autorole'] = {
                        "linked": self.locals['roles']['DCS'][0]
                    }
            with open(os.path.join('config', 'services', self.name + '.yaml'), mode='w', encoding='utf-8') as outfile:
                yaml.dump(self.locals, outfile)

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
        from services import ServiceBus

        await super().start()
        try:
            while not ServiceRegistry.get(ServiceBus):
                await asyncio.sleep(1)
            self.bot = self.init_bot()
            await self.install_fonts()
            async with self.bot:
                await self.bot.start(self.locals['token'], reconnect=reconnect)
        except PermissionError as ex:
            self.log.error("Please check the permissions for " + str(ex))
            raise
        except discord.HTTPException:
            self.log.error("Error while logging in your Discord bot. Check you token!")
            raise
        except Exception as ex:
            self.log.exception(ex)
            raise

    async def stop(self):
        if self.bot:
            await self.bot.close()
        await super().stop()

    async def alert(self, title: str, message: str, server: Optional[Server] = None,
                    node: Optional[str] = None) -> None:
        mentions = ''.join([self.bot.get_role(role).mention for role in self.bot.roles['Alert']])
        embed, file = utils.create_warning_embed(title=title, text=utils.escape_string(message))
        if not server and node:
            try:
                server = next(server for server in self.bot.servers.values() if server.node.name == node)
            except StopIteration:
                server = None
        if server:
            await self.bot.get_admin_channel(server).send(content=mentions, embed=embed, file=file)

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
        if not _channel:
            return
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

    async def audit(self, message, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None):
        await self.bot.audit(message, user=user, server=server)

    async def rename_server(self, server: Server, new_name: str):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # call rename() in all Plugins
                for plugin in self.bot.cogs.values():  # type: Plugin
                    await plugin.rename(conn, server.name, new_name)
