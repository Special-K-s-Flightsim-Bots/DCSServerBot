import asyncio
import importlib
import importlib.util

from core import NodeImpl, ServiceRegistry, EventListener, Server, Plugin, PluginError
from typing import Any

from services.bot.dummy import DummyGuild, DummyMember, DummyRole


class DummyBot:

    def __init__(self, version: int, sub_version: int, node: NodeImpl, locals: dict):
        from services.servicebus import ServiceBus

        self.closed = False
        self.version = version
        self.sub_version = sub_version
        self.node = node
        self.log = node.log
        self.pool = node.pool
        self.apool = node.apool
        self.plugins = self.node.plugins
        self.locals = locals | {'automatch': False}
        self.bus = ServiceRegistry.get(ServiceBus)
        self.eventListeners: set[EventListener] = self.bus.eventListeners
        self.loop = self.bus.loop
        self._roles = None
        self.setup = asyncio.Event()
        asyncio.create_task(self.start())
        self.cogs: dict[str, Plugin] = {}
        self.guilds = [DummyGuild()]
        self.owner_id = -1
        self.latency = 0
        self.member = DummyMember("1", name="DCSServerBot")

    async def start(self):
        self.log.warning("This installation does not use a Discord bot!")
        self.setup.clear()
        asyncio.create_task(self.setup_hook())

    async def close(self):
        for plugin in self.cogs.values():
            await plugin.cog_unload()
        self.closed = True

    async def login(self, token: str) -> None:
        ...

    async def connect(self, **kwargs) -> None:
        ...

    async def wait_until_ready(self) -> None:
        await self.setup.wait()

    def is_closed(self) -> bool:
        return self.closed

    @property
    def roles(self) -> dict[str, list[str | int]]:
        _roles = {
            "Admin": ["Admin"],
            "DCS Admin": ["DCS Admin"]
        } | {name: [name] for name in self.guilds[0]._roles.keys()}
        if 'GameMaster' not in _roles:
            _roles['GameMaster'] = ['DCS Admin']
        if 'Alert' not in _roles:
            _roles['Alert'] = ['DCS Admin']
        return _roles

    @property
    def filter(self) -> dict:
        return self.bus.filter

    @property
    def servers(self) -> dict[str, Server]:
        return self.bus.servers

    async def add_cog(self, plugin: Plugin) -> None:
        self.cogs[plugin.__class__.__name__] = plugin
        await plugin.cog_load()

    async def load_plugin(self, plugin_name: str) -> bool:
        module = importlib.import_module(f"plugins.{plugin_name}.commands")
        if hasattr(module, 'setup'):
            try:
                await module.setup(self)
                return True
            except PluginError as ex:
                self.log.error(f'  - {ex}')
            except Exception as ex:
                self.log.error(f'  - Plugin "{plugin_name.title()} not loaded!', exc_info=ex)
            return False
        else:
            self.log.error(f"No 'setup' function in {plugin_name}")
            return False

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        for plugin in self.plugins:
            if not await self.load_plugin(plugin.lower()):
                self.log.info(f'  => {plugin.title()} NOT loaded.')
        # cleanup remote servers (if any)
        for key in [key for key, value in self.bus.servers.items() if value.is_remote]:
            self.bus.servers.pop(key)
        self.setup.set()

    async def audit(self, message, *, user: Any = None, server: Server | None = None, **kwargs):
        ...

    def get_admin_channel(self, server: Server) -> None:
        ...

    async def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> DummyMember | None:
        return self.get_member_by_ucid(ucid, verified)

    async def get_ucid_by_member(self, member: DummyMember, _verified: bool | None = False) -> str:
        return member.id

    def get_member_by_ucid(self, ucid: str, _verified: bool | None = False) -> DummyMember | None:
        return self.guilds[0].get_member(ucid)

    def match_user(self, data: dict, rematch=False) -> None:
        ...

    def get_server(self, ctx: Any, *, admin_only: bool | None = False) -> None:
        ...

    async def setEmbed(self, *, embed_name: str, embed: Any, channel_id: Any, file: Any, server: Any):
        ...

    def get_role(self, role: str | int) -> DummyRole | None:
        return self.guilds[0]._roles[role]

    def get_channel(self, channel_id: int) -> None:
        ...

    async def fetch_user(self, ucid: str) -> DummyMember | None:
        return await self.guilds[0].fetch_member(ucid)

    def add_command(self, command: Any, /) -> None:
        ...

    def remove_command(self, name: str, /) -> None:
        ...

    async def fetch_channel(self, channel_id: int, /) -> None:
        ...
