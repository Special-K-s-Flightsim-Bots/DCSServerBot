import aiohttp
import asyncio
import base64
import certifi
import discord
import os
import secrets
import ssl
import time

from contextlib import suppress
from core import (Plugin, Group, PluginRequiredError, utils, get_translation, ServiceRegistry, PluginInstallationError,
                  ServiceProxy, DEFAULT_TAG, Server)
from discord import app_commands
from fastapi import FastAPI, APIRouter, HTTPException, Depends
from pathlib import Path
from plugins.dks.views import RegisterView
from plugins.dks.auth import TokenBearer
from plugins.restapi.commands import RestAPI
from plugins.restapi.links import Link, link_reach, match_key, member_reach, parse_links
from starlette.requests import Request
from typing import Any, cast
from urllib.parse import quote, urlparse

from services.bot import DCSServerBot
from services.webservice import WebService
from services.webservice.registry import exclude_router, include_router_counted

from core.utils.yaml_helper import new_yaml

_ = get_translation(__name__.split('.')[1])
DKS_URL = "https://www.digitalkneeboardsimulator.com/api/register/dcssb?otp={otp}&connection_string={callback_url}"
DKS_JWKS_URL = "https://www.digitalkneeboardsimulator.com/api/pubkey"
_CLAIM_LOCK = asyncio.Lock()


class DKS(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'webservice.yaml')):
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not configured")

        self.web_service: WebService | None = None
        self.app: FastAPI | None = None
        self.router: APIRouter | None = None
        #: the route object(s) THIS plugin's include appended to the app (never the router's own
        #: routes) — the only thing that can be removed again on unload
        self._included: list[Any] = []
        # unclaimed registration links: otp -> {user_id, owner (None = bot-wide), expires, msg}
        self._pending: dict[str, dict] = {}

    async def cog_load(self) -> None:
        await super().cog_load()
        asyncio.create_task(self.init_webservice())

    async def cog_unload(self) -> None:
        # remove exactly what this instance installed, by identity: the previous handler would keep
        # answering otherwise, with its own stale auth decisions
        if self.app and self._included:
            exclude_router(self.app, self._included)
            self._included = []
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
        app = self.app
        if app is None:
            return
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

        # a router installed by an earlier call on THIS instance is replaced, not added to: its
        # handlers carry that call's auth dependencies
        if self._included:
            exclude_router(app, self._included)
            self._included = []
        self._included = include_router_counted(app, self.router)

    def generate_otp(self, user_id: int, owner: int | None, ttl: int = 300) -> str:
        now = time.monotonic()
        # one open link per user: customers who register at the same time don't cancel each other
        self._pending = {k: v for k, v in self._pending.items() if v['expires'] > now and v['user_id'] != user_id}
        otp = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        self._pending[otp] = {"user_id": user_id, "owner": owner, "expires": now + ttl, "msg": None}
        return otp

    def claim_otp(self, otp) -> dict | None:
        pending = self._pending.pop(otp, None) if isinstance(otp, str) else None
        return pending if pending and pending['expires'] > time.monotonic() else None

    def match_link(self, key) -> Link | None:
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))
        return match_key(parse_links(restapi.get_config().get('auth', {}).get('jwt')), key)

    @staticmethod
    async def update_embed(pending: dict, message: str):
        with suppress(discord.NotFound):
            if pending['msg']:
                await pending['msg'].edit(content=message, embed=None, view=None)
                pending['msg'] = None

    def _get_prefix(self) -> str:
        prefix = cast(RestAPI, self.bot.cogs.get('RestAPI')).get_config().get('prefix', '')
        return f"/{prefix}" if prefix and not prefix.startswith('/') else prefix

    async def register_dks(self, request: Request):
        pending = request.state.dks_registration
        # one claim at a time: two overlapping RestAPI reloads make the second fail, which restarts the bot
        async with _CLAIM_LOCK:
            try:
                jwt_payload = request.state.jwt_payload
                key = jwt_payload.get('key')

                config = os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml')
                if os.path.exists(config):
                    data = new_yaml().load(Path(config).read_text(encoding='utf-8'))
                else:
                    data = {}
                auth = data.setdefault(DEFAULT_TAG, {}).setdefault('auth', {})
                jwt_config = auth['jwt'] = auth.get('jwt') or {}
                jwt_config['jwks_url'] = DKS_JWKS_URL
                if pending['owner'] is None:
                    # an Admin's registration: the bot-wide key
                    jwt_config['key'] = key
                else:
                    # a DCS Admin's: their link, replacing their previous one
                    jwt_config['links'] = [x for x in jwt_config.get('links') or []
                                           if str(x.get('owner')) != str(pending['owner'])] + [{"key": key, "owner": pending['owner']}]

                tmp = Path(config).with_suffix('.yaml.tmp')
                with tmp.open('w', encoding='utf-8') as f:
                    new_yaml().dump(data, f)
                os.replace(tmp, config)

                if await self.bot.reload_plugin("RestAPI"):
                    await self.update_embed(pending, _("Your bot is now connected to DKS!"))
                else:
                    await self.update_embed(pending, _("Restarting DCSServerBot ..."))
                    await self.node.restart()

                return {
                    "guild_id": self.node.guild_id,
                    "owner": self.bot.owner_id
                }
            except Exception as ex:
                self.log.exception(ex)
                asyncio.create_task(self.update_embed(pending, _("Error while registering with DKS: {}!").format(ex)))
                raise HTTPException(status_code=500, detail='Error while registering.')

    @staticmethod
    async def tacview_enabled(server: Server) -> bool:
        # From nodes.yaml: list_extensions() is cached on remote nodes.
        config = (await server.config_extension('Tacview'))['Instance']
        return bool(config) and config.get('enabled', True)

    async def install_dks_exporter(self, request: Request):
        """DKS asks to install its exporter. The install code is fetched from DKS over HTTPS with a single-use claim.
        The parameters are claims of DKS's signed token, never an unsigned body. Only for servers an Admin allowed it
        on (/dks install_permission)."""
        payload = request.state.jwt_payload
        server_name, claim, code = payload.get('server'), payload.get('claimUrl'), payload.get('code')
        if not all(isinstance(x, str) and x for x in [server_name, claim, code]):
            raise HTTPException(status_code=400, detail="server, claimUrl and code are missing")
        server = self.bot.servers.get(server_name)
        reach = await link_reach(self.bot, request.state.dks_link)
        if not server or (reach is not None and server_name not in reach):
            raise HTTPException(status_code=404, detail=f"Server {server_name} not found")
        if not self.get_config(server).get('allow_install', False):
            raise HTTPException(status_code=409, detail=f"Installs from DKS are off for {server_name}. An Admin or "
                                                        f"DCS Admin can allow them with /dks install_permission in "
                                                        f"Discord.")
        if urlparse(claim).scheme != 'https':
            raise HTTPException(status_code=400, detail="The claim URL must be https")
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.post(claim, json={"code": code}, raise_for_status=True,
                                        proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                    install_code = (await response.json())['installCode']
        except Exception as ex:
            self.log.error(f"DKS: Couldn't fetch the install code: {ex}")
            raise HTTPException(status_code=502, detail="The bot couldn't fetch the install code from DKS")

        # The extension downloads the exporter, then disables the Tacview extension (DKS replaces it).
        tacview_before = await self.tacview_enabled(server)
        await server.config_extension('DKS', {"Instance": {"enabled": True, "key": install_code}})
        try:
            installed = await server.enable_extension('DKS')
        except Exception as ex:
            self.log.exception(ex)
            installed = False
        if not installed:
            await server.config_extension('DKS', {"Instance": {"enabled": False}})
            raise HTTPException(status_code=500, detail="Installing the DKS exporter failed, see the bot's log")
        tacview_after = await self.tacview_enabled(server)
        await self.bot.audit(f"DKS exporter installed on server {server.display_name} from DKS.")
        return {
            "tacviewDisabled": tacview_before and not tacview_after
        }

    dks = Group(name="dks", description=_("Commands to manage Digital Kneeboard Simulator"))

    @dks.command(name="register")
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin', 'DCS Admin'])
    async def register(self, interaction: discord.Interaction):
        # an Admin links the whole bot, a DCS Admin the servers they manage
        reach = member_reach(self.bot, interaction.user)
        if reach is not None and not reach:
            await interaction.response.send_message(_("You don't manage any server on this bot."), ephemeral=True)
            return
        owner = None if reach is None else interaction.user.id
        restapi = cast(RestAPI, self.bot.cogs.get('RestAPI'))
        if any(link.owner == owner for link in parse_links(restapi.get_config().get('auth', {}).get('jwt'))):
            if not await utils.yn_question(
                interaction,
                message=_("You have a JWT key configured for your RestAPI service already.") if owner is None
                else _("You have linked your servers to DKS already."),
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
        otp = self.generate_otp(interaction.user.id, owner)
        url = DKS_URL.format(otp=otp, callback_url=quote(callback_url))
        embed = discord.Embed(
            color=discord.Color.blue(),
            title="Digital Kneeboard Simulator",
            url="https://www.digitalkneeboardsimulator.com/"
        )
        embed.description = _("To register your bot with the DKS website,\nplease klick `Register` below.")
        servers = _("All servers") if reach is None else ", ".join(sorted(reach))
        embed.add_field(name=_("Servers"), value=servers[:1024], inline=False)
        embed.add_field(name=_("Attention!"), value=_("Your registration link will be invalidated after 5 minutes."))
        embed.set_thumbnail(url="https://www.digitalkneeboardsimulator.com/logo.png")
        view = RegisterView(url=url)
        # ephemeral: whoever opens an unclaimed link can link these servers to their own squadron
        self._pending[otp]['msg'] = await interaction.followup.send(embed=embed, view=view, ephemeral=True)


    @dks.command(name="install_permission", description=_("Allow DKS to install its exporter on a server"))
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin', 'DCS Admin'])
    async def install_permission(self, interaction: discord.Interaction,
                                 server: app_commands.Transform[Server, utils.ServerTransformer], allow: bool):
        reach = member_reach(self.bot, interaction.user)
        if reach is not None and server.name not in reach:
            await interaction.response.send_message(_("Server {} not found.").format(server.name), ephemeral=True)
            return
        config = os.path.join(self.node.config_dir, 'plugins', 'dks.yaml')
        if os.path.exists(config):
            data = new_yaml().load(Path(config).read_text(encoding='utf-8')) or {}
        else:
            data = {}
        # where get_base_config reads it: the node's block if the file has one, else the flat instance entry
        target = data[server.node.name] if server.node.name in data else data
        target.setdefault(server.instance.name, {})['allow_install'] = allow
        tmp = Path(config).with_suffix('.yaml.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            new_yaml().dump(data, f)
        os.replace(tmp, config)
        self.locals = self.read_locals()
        self._config.clear()
        if allow:
            await interaction.response.send_message(
                _("DKS can now install its exporter on {}, replacing Tacview.").format(server.display_name),
                ephemeral=True)
        else:
            await interaction.response.send_message(
                _("DKS can no longer install its exporter on {}.").format(server.display_name), ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'restapi' not in bot.plugins:
        raise PluginRequiredError('restapi')

    await bot.add_cog(DKS(bot))
