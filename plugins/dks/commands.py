import asyncio
import base64
import json
import aiohttp
import discord
import os
import secrets

from asyncio import Task
from contextlib import suppress
from core import Plugin, Group, PluginRequiredError, utils, get_translation, ServiceRegistry, PluginInstallationError, \
    ServiceProxy, DEFAULT_TAG
from fastapi import FastAPI, APIRouter, HTTPException, Depends
from pathlib import Path
from plugins.dks.views import RegisterView
from plugins.dks.auth import TokenBearer
from plugins.restapi.commands import RestAPI
from starlette.requests import Request
from typing import cast
from urllib.parse import quote

from services.bot import DCSServerBot
from services.webservice import WebService

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])
DKS_URL = "https://www.digitalkneeboardsimulator.com/api/register/dcssb?otp={otp}&connection_string={callback_url}"
DKS_JWKS_URL = "https://digitalkneeboardsimulator.com/api/pubkey"


class DKS(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'webservice.yaml')):
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not configured")

        self.web_service: WebService | None = None
        self.app: FastAPI | None = None
        self.router: APIRouter | None = None
        # JWKS
        self.jwks = None
        # Simple OTP handling
        self._otp: str | None = None
        self._task: Task | None = None
        # embed to be updated
        self.msg: discord.Message | None = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.jwks = await self.read_jwks()
        asyncio.create_task(self.init_webservice())

    async def cog_unload(self) -> None:
        if self.app and self.router:
            for route in self.router.routes:
                if route in self.app.routes:
                    self.app.routes.remove(route)
        await super().cog_unload()

    async def read_jwks(self) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    DKS_JWKS_URL,
                    raise_for_status=True,
                    proxy=self.node.proxy,
                    proxy_auth=self.node.proxy_auth
            ) as response:
                return json.loads(await response.read())

    async def init_webservice(self):
        # give the webservice 10 seconds to launch on master switches
        for i in range(0, 10):
            self.web_service = ServiceRegistry.get(WebService)
            if isinstance(self.web_service, ServiceProxy):
                return
            if self.web_service and self.web_service.is_running():
                break
            await asyncio.sleep(0.1)
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not running, aborted.")
            return
        self.log.debug(f"   - {self.__cog_name__}: WebService is running")
        self.app = self.web_service.app
        # Wait for the RestAPI to be loaded
        while not self.bot.cogs.get("RestAPI"):
            await asyncio.sleep(0.1)

        if self.app:
            self.register_routes()
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not available, aborted.")
            return

    def register_routes(self):
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))

        prefix = restapi.get_config().get('prefix', '')
        if prefix and not prefix.startswith('/'):
            prefix = '/' + prefix

        self.router = APIRouter(prefix=prefix)
        if not self.router:
            return

        # /register_dks
        self.router.add_api_route(
            "/register_dks", self.register_dks,
            methods=["POST"],
            description="Register with Digital Kneeboard Simulator.",
            tags=["DKS"],
            dependencies=[Depends(TokenBearer(plugin=self))]
        )

        self.app.include_router(self.router)

    @property
    def otp(self) -> str | None:
        try:
            return self._otp
        finally:
            if self._task and not self._task.done():
                self._task.cancel()
                self._task = None
            self._otp = None

    def generate_otp(self, ttl: int = 300) -> str:
        async def cleanup_otp(timeout: int):
            await asyncio.sleep(timeout)
            self._otp = None

        # clear any existing otp
        self._otp = self.otp
        secret = secrets.token_bytes(32)
        self._otp = base64.urlsafe_b64encode(secret).decode('utf-8').rstrip('=')
        self._task = asyncio.create_task(cleanup_otp(ttl))
        return self._otp

    async def update_embed(self, message: str):
        with suppress(discord.NotFound):
            if self.msg:
                await self.msg.edit(content=message, embed=None, view=None)
                self.msg = None

    async def register_dks(self, request: Request):
        try:
            jwt_payload = request.state.jwt_payload
            key = jwt_payload.get('key')

            config = os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml')
            data = yaml.load(Path(config).read_text(encoding='utf-8'))
            data.setdefault(DEFAULT_TAG, {})['auth'].update({
                "jwt": {
                    "jwks": self.jwks,
                    "key": key
                }
            })

            with Path(config).open(mode='w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)

            asyncio.create_task(self.bot.reload_plugin("RestAPI"))
            asyncio.create_task(self.update_embed(_("Your bot is now connected to DKS!")))

            return {
                "guild_id": self.node.guild_id,
                "owner": self.bot.owner_id
            }
        except Exception as ex:
            self.log.exception(ex)
            asyncio.create_task(self.update_embed(_("Error while registering with DKS: {}!").format(ex)))
            raise HTTPException(status_code=500, detail='Error while registering.')

    dks = Group(name="dks", description=_("Commands to manage Digital Kneeboard Simulator"))

    @dks.command(name="register")
    async def register(self, interaction: discord.Interaction):
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))
        if restapi.get_config().get('jwt') and not await utils.yn_question(
                interaction,
                message=_("You have a JWT key configured for your RestAPI service already."),
                question=_("Do you want to overwrite it?")
            ):
            await interaction.followup.send(_("Aborted"), ephemeral=True)
            return
        else:
            await interaction.response.defer(ephemeral=True)

        webservice: WebService | None = ServiceRegistry.get(WebService)
        if not webservice:
            await interaction.followup.send(_("WebSevice is not active!"), ephemeral=True)
            return

        callback_url = self.get_config().get('callback_url')
        if not callback_url:
            host = self.get_config().get('host', f"http://{webservice.node.public_ip}")
            port = webservice.get_ports()['WebService'].port
            callback_url = f"{host}:{port}/register_dks"
        url = DKS_URL.format(otp=self.generate_otp(), callback_url=quote(callback_url))
        embed = discord.Embed(
            color=discord.Color.blue(),
            title="Digital Kneeboard Simulator",
            url="https://www.digitalkneeboardsimulator.com/"
        )
        embed.description = _("To register your bot with the DKS website,\nplease klick `Register` below.")
        embed.add_field(name=_("Attention!"), value=_("Your registration link will be invalidated after 5 minutes."))
        embed.set_thumbnail(url="https://www.digitalkneeboardsimulator.com/logo.png")
        view = RegisterView(url=url)
        self.msg = await interaction.followup.send(embed=embed, view=view)


async def setup(bot: DCSServerBot):
    if 'restapi' not in bot.plugins:
        raise PluginRequiredError('restapi')

    await bot.add_cog(DKS(bot))
