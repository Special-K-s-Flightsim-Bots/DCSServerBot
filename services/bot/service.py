from __future__ import annotations

import asyncio
import discord
import os
import zipfile

from aiohttp import BasicAuth
from core import utils, FatalException
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import commands
from discord.utils import MISSING
from io import BytesIO
from matplotlib import font_manager
from pathlib import Path
from ssl import SSLCertVerificationError
from typing import TYPE_CHECKING

from .dcsserverbot import DCSServerBot
from .dummy import DummyBot
from ..servicebus import ServiceBus

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

if TYPE_CHECKING:
    from core import Server, Plugin, Node

__all__ = ["BotService"]


@ServiceRegistry.register(master_only=True, depends_on=[ServiceBus])
class BotService(Service):

    def _migrate_autorole(self) -> bool:
        if not isinstance(self.locals.get('autorole'), str):
            return False
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
        return True

    def _secure_token(self) -> bool:
        token = self.locals.pop('token', None)
        if not token:
            return False
        self.log.info("Discord TOKEN found, removing it from yaml ...")
        utils.set_password('token', token, self.node.config_dir)
        return True

    def _secure_proxy_pass(self) -> bool:
        password = self.locals.get('proxy', {}).pop('password', None)
        if not password:
            return False
        self.log.info("Proxy password found, removing it from yaml ...")
        utils.set_password('proxy', password, self.node.config_dir)
        return True

    def __init__(self, node):
        super().__init__(node=node, name="Bot")
        self.bot: DCSServerBot | None = None
        # do we need to change the bot.yaml file?
        dirty = self._migrate_autorole()
        dirty = self._secure_token() or dirty
        dirty = self._secure_proxy_pass() or dirty
        if dirty:
            self.save_config()

    @property
    def token(self) -> str | None:
        try:
            return utils.get_password('token', self.node.config_dir)
        except ValueError:
            return None

    @property
    def proxy(self) -> str | None:
        return self.locals.get('proxy', {}).get('url')

    @property
    def proxy_auth(self) -> BasicAuth | None:
        username = self.locals.get('proxy', {}).get('username')
        try:
            password = utils.get_password('proxy', self.node.config_dir)
        except ValueError:
            return None
        if username and password:
            return BasicAuth(username, password)
        return None

    def init_bot(self):
        if self.locals.get('no_discord', False):
            return DummyBot(version=self.node.bot_version,
                            sub_version=self.node.sub_version,
                            node=self.node,
                            locals=self.locals)
        else:
            def get_prefix(client, message):
                prefixes = []
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
                                activity=discord.Game(
                                    name=self.locals['discord_status']) if 'discord_status' in self.locals else None,
                                heartbeat_timeout=120,
                                assume_unsync_clock=True,
                                proxy=self.proxy,
                                proxy_auth=self.proxy_auth)

    async def start(self, *, reconnect: bool = True) -> None:
        await super().start()
        try:
            self.bot = self.init_bot()
            await self.install_fonts()
            await self.bot.login(token=self.token)
            asyncio.create_task(self.bot.connect(reconnect=reconnect))
        except Exception as ex:
            self.log.exception(ex)
        except PermissionError as ex:
            self.log.error("Please check the permissions for " + str(ex))
            raise
        except discord.HTTPException:
            raise FatalException("Error while logging in your Discord bot. Check you token!")
        except SSLCertVerificationError:
            raise FatalException("The Discord certificate is invalid. You need to import it manually. "
                                 "Check the known issues section in my Discord for help.")

    async def stop(self):
        if self.bot:
            await self.bot.close()
        await super().stop()

    async def alert(self, title: str, message: str, server: Server | None = None) -> None:
        # if we have dedicated managers of a server, send the alerts to them
        if server and server.locals.get('managed_by'):
            alert_roles = server.locals['managed_by']
        # use the default Alert role otherwise
        else:
            alert_roles = self.bot.roles['Alert']
        try:
            mentions = ''.join([self.bot.get_role(role).mention for role in alert_roles if role is not None])
        except AttributeError:
            self.log.error(f"Alert-Role {alert_roles} not found.")
            mentions = ""
        embed = utils.create_warning_embed(title=title, text=utils.escape_string(message))
        admin_channel = self.bot.get_admin_channel(server)
        audit_channel = self.bot.get_channel(self.bot.locals.get('channels', {}).get('audit', -1))
        channel = admin_channel or audit_channel
        if channel:
            await channel.send(content=mentions, embed=embed)
        else:
            self.log.critical(f"{title}: {message}")

    async def install_fonts(self):
        font_dir = Path('fonts')
        if not font_dir.exists() or not font_dir.is_dir():
            return
        for file in Path('fonts').rglob('Noto_Sans_*.zip'):
            self.log.info(f"  - Unpacking {file} ...")
            with zipfile.ZipFile(file, 'r') as zip_ref:
                # Extract all files
                for member in zip_ref.namelist():
                    # Check if file is in 'static' directory
                    if member.startswith('static/'):
                        with zip_ref.open(member) as file_to_extract:
                            file_path = font_dir / Path(member).name
                            with open(file_path, 'wb') as new_file:
                                new_file.write(file_to_extract.read())
            file.unlink()
        for f in font_manager.findSystemFonts('fonts'):
            font_manager.fontManager.addfont(f)

    async def send_message(self, channel: int | None = -1, content: str | None = None,
                           server: Server | None = None, filename: str | None = None,
                           embed: dict | None = None, mention: list | None = None):
        _channel = self.bot.get_channel(channel)
        if not _channel:
            if channel and channel != -1:
                raise ValueError(f"Channel {channel} not found!")
            elif self.bot.audit_channel:
                _channel = self.bot.audit_channel
            else:
                return
        _embed = discord.Embed.from_dict(embed) if embed else MISSING
        if filename:
            data = await server.node.read_file(filename)
            file = discord.File(BytesIO(data), filename=os.path.basename(filename))
        else:
            file = MISSING
        if mention:
            _mention = "".join([self.bot.get_role(role).mention for role in mention])
            content = _mention + (content or "")
        await _channel.send(content=content, file=file, embed=_embed)

    async def audit(self, message, user: discord.Member | str | None = None,
                    server: Server | None = None, node: Node | None = None, **kwargs):
        await self.bot.audit(message, user=user, server=server, node=node, **kwargs)

    async def rename_server(self, server: Server, new_name: str):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # call rename() in all Plugins
                for plugin in self.bot.cogs.values():  # type: Plugin
                    await plugin.rename(conn, server.name, new_name)
