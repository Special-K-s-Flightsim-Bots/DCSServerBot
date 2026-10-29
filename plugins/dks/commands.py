import aiohttp
import asyncio
import base64
import certifi
import discord
import os
import secrets
import ssl

from asyncio import Task
from contextlib import suppress
from core import (Plugin, Group, PluginRequiredError, utils, get_translation, ServiceRegistry, PluginInstallationError,
                  ServiceProxy, DEFAULT_TAG)
from discord import app_commands
from fastapi import FastAPI, APIRouter, HTTPException, Depends
from pathlib import Path
from pydantic import BaseModel
from plugins.dks.views import RegisterView
from plugins.dks.auth import TokenBearer
from plugins.restapi.commands import RestAPI
from starlette.requests import Request
from typing import cast
from urllib.parse import quote, urlparse

from services.bot import DCSServerBot
from services.webservice import WebService

from core.utils.yaml_helper import new_yaml

_ = get_translation(__name__.split('.')[1])
DKS_URL = "https://www.digitalkneeboardsimulator.com/api/register/dcssb?otp={otp}&connection_string={callback_url}"
DKS_JWKS_URL = "https://www.digitalkneeboardsimulator.com/api/pubkey"


class InstallExporter(BaseModel):
    server: str
    claimUrl: str
    code: str


class DKS(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'webservice.yaml')):
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not configured")

        self.web_service: WebService | None = None
        self.app: FastAPI | None = None
        self.router: APIRouter | None = None
        # Simple OTP handling
        self._otp: str | None = None
        self._task: Task | None = None
        # embed to be updated
        self.msg: discord.Message | None = None

    async def cog_load(self) -> None:
        await super().cog_load()
        asyncio.create_task(self.init_webservice())

    async def cog_unload(self) -> None:
        if self.app and self.router:
            for route in self.router.routes:
                if route in self.app.routes:
                    self.app.routes.remove(route)
        await super().cog_unload()

    async def init_webservice(self):
        # give the webservice 10 seconds to launch on master switches
        for i in range(0, 10):
            self.web_service = ServiceRegistry.get(WebService)
            if isinstance(self.web_service, ServiceProxy):
                return
            if self.web_service and self.web_service.is_running():
                break
            await asyncio.sleep(1)
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not running, aborted.")
            return
        self.log.debug(f"   - {self.__cog_name__}: WebService is running")
        self.app = self.web_service.app
        # Wait for the RestAPI to be loaded
        while not self.bot.cogs.get("RestAPI"):
            await asyncio.sleep(0.1)

        # also, wait for the bot before exposing API endpoints
        await self.bot.wait_until_ready()
        if self.app:
            self.register_routes()
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not available, aborted.")
            return

    def register_routes(self):
        self.router = APIRouter(prefix=self._get_prefix())
        if not self.router:
            return

        # /register_dks
        self.router.add_api_route(
            "/register_dks", self.register_dks,
            methods=["POST"],
            summary="Register with DKS.",
            description="Register with Digital Kneeboard Simulator.",
            tags=["DKS"],
            dependencies=[Depends(TokenBearer(plugin=self, jwks_url=DKS_JWKS_URL))]
        )

        # /install_dks_exporter
        self.router.add_api_route(
            "/install_dks_exporter", self.install_dks_exporter,
            methods=["POST"],
            summary="Install the DKS exporter.",
            description="Install the DKS exporter on a server, replacing Tacview.",
            tags=["DKS"],
            dependencies=[Depends(TokenBearer(plugin=self, jwks_url=DKS_JWKS_URL, linked=True))]
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

    def _get_prefix(self) -> str:
        prefix = cast(RestAPI, self.bot.cogs.get('RestAPI')).get_config().get('prefix', '')
        return f"/{prefix}" if prefix and not prefix.startswith('/') else prefix

    async def register_dks(self, request: Request):
        try:
            jwt_payload = request.state.jwt_payload
            key = jwt_payload.get('key')

            config = os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml')
            if os.path.exists(config):
                data = new_yaml().load(Path(config).read_text(encoding='utf-8'))
            else:
                data = {}
            data.setdefault(DEFAULT_TAG, {}).setdefault('auth', {}).update({
                "jwt": {
                    "jwks_url": DKS_JWKS_URL,
                    "key": key
                }
            })

            tmp = Path(config).with_suffix('.yaml.tmp')
            with tmp.open('w', encoding='utf-8') as f:
                new_yaml().dump(data, f)
            os.replace(tmp, config)

            if await self.bot.reload_plugin("RestAPI"):
                await self.update_embed(_("Your bot is now connected to DKS!"))
            else:
                await self.update_embed(_("Restarting DCSServerBot ..."))
                await self.node.restart()

            return {
                "guild_id": self.node.guild_id,
                "owner": self.bot.owner_id
            }
        except Exception as ex:
            self.log.exception(ex)
            asyncio.create_task(self.update_embed(_("Error while registering with DKS: {}!").format(ex)))
            raise HTTPException(status_code=500, detail='Error while registering.')

    @property
    def linked_key(self) -> str | None:
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))
        return restapi.get_config().get('auth', {}).get('jwt', {}).get('key') if restapi else None

    async def install_dks_exporter(self, body: InstallExporter):
        """DKS asks to install its exporter. The install code is fetched from DKS over HTTPS with a single-use claim,
        so it never travels through this API."""
        server = self.bot.servers.get(body.server)
        if not server:
            raise HTTPException(status_code=404, detail=f"Server {body.server} not found")
        claim_url = urlparse(body.claimUrl)
        if claim_url.scheme != 'https' and claim_url.hostname not in ['localhost', '127.0.0.1']:
            raise HTTPException(status_code=400, detail="The claim URL must be https")
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.post(body.claimUrl, json={"code": body.code}, raise_for_status=True,
                                        proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                    install_code = (await response.json())['installCode']
        except Exception as ex:
            self.log.error(f"DKS: Couldn't fetch the install code: {ex}")
            raise HTTPException(status_code=502, detail="The bot couldn't fetch the install code from DKS")

        # The DKS exporter replaces Tacview. Changing the config only keeps Tacview's files while DCS runs.
        tacview_disabled = 'Tacview' in await server.list_extensions(active=True)
        if tacview_disabled:
            await server.config_extension('Tacview', {"Instance": {"enabled": False}})
        await server.config_extension('DKS', {"Instance": {"enabled": True, "key": install_code}})
        if not await server.enable_extension('DKS'):
            raise HTTPException(status_code=500, detail="Installing the DKS exporter failed, see the bot's log")
        ext = server.extensions.get('DKS')
        await self.bot.audit(f"DKS exporter installed on server {server.display_name} from DKS.")
        return {
            "tacviewDisabled": tacview_disabled,
            "version": ext.version if ext else None
        }

    dks = Group(name="dks", description=_("Commands to manage Digital Kneeboard Simulator"))

    @dks.command(name="register")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def register(self, interaction: discord.Interaction):
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))
        if restapi.get_config().get('auth', {}).get('jwt'):
            if not await utils.yn_question(
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
            callback_url = f"{host}:{port}{self._get_prefix()}/register_dks"
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
