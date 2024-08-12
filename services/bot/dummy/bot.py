import asyncio
import importlib
import importlib.util

from core import NodeImpl, ServiceRegistry, EventListener, Server, Plugin
from typing import Union, Optional, Any

from services.bot.dummy import DummyGuild, DummyMember, DummyRole


class DummyBot:

    def __init__(self, version: int, sub_version: int, node: NodeImpl, locals: dict):
        from services import ServiceBus

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
        self.eventListeners: list[EventListener] = self.bus.eventListeners
        self.loop = self.bus.loop
        self._roles = None
        self.setup = asyncio.Event()
        asyncio.create_task(self.start())
        self.cogs: dict[str, Plugin] = {}
        self.guilds = [DummyGuild()]
        self.owner_id = -1
        self.latency = 0

    async def start(self):
        self.log.warning("This installation does not use a Discord bot!")
        self.setup.clear()
        # noinspection PyAsyncCall
        asyncio.create_task(self.setup_hook())

    async def stop(self):
        for plugin in self.cogs.values():
            await plugin.cog_unload()
        await self.close()

    async def login(self, token: str) -> None:
        ...

    async def connect(self, **kwargs) -> None:
        ...

    async def wait_until_ready(self) -> None:
        await self.setup.wait()

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    @property
    def roles(self) -> dict[str, list[Union[str, int]]]:
        _roles = {
            "Admin": ["Admin"],
            "DCS Admin": ["DCS Admin"]
        } | {name: [name] for name in self.guilds[0]._roles.keys()}
        if 'GameMaster' not in _roles:
            _roles['GameMaster'] = ['DCS Admin']
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
            await module.setup(self)
            return True
        else:
            self.log.error(f"No 'setup' function in {plugin_name}")
            return False

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        for plugin in self.plugins:
            if not await self.load_plugin(plugin.lower()):
                self.log.info(f'  => {plugin.title()} NOT loaded.')
        # cleanup remote servers (if any)
        for key, value in self.bus.servers.items():
            if value.is_remote:
                del self.bus.servers[key]
        self.setup.set()

    async def audit(self, message, *, user: Any = None, server: Optional[Server] = None, **kwargs):
        ...

    def get_admin_channel(self, server: Server) -> None:
        ...

    async def get_ucid_by_name(self, name: str) -> tuple[Optional[str], Optional[str]]:
        async with self.apool.connection() as conn:
            search = f'%{name}%'
            cursor = await conn.execute("""
                SELECT ucid, name FROM players 
                WHERE LOWER(name) like LOWER(%s) 
                ORDER BY last_seen DESC LIMIT 1
            """, (search, ))
            if cursor.rowcount >= 1:
                res = await cursor.fetchone()
                return res[0], res[1]
            else:
                return None, None

    async def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> Optional[DummyMember]:
        return self.get_member_by_ucid(ucid, verified)

    async def get_ucid_by_member(self, member: DummyMember, verified: Optional[bool] = False) -> str:
        return member.id

    def get_member_by_ucid(self, ucid: str, verified: Optional[bool] = False) -> Optional[DummyMember]:
        return self.guilds[0].get_member(ucid)

    def match_user(self, data: dict, rematch=False) -> None:
        ...

    def get_server(self, ctx: Any, *, admin_only: Optional[bool] = False) -> None:
        ...

    async def setEmbed(self, *, embed_name: str, embed: Any, channel_id: Any, file: Any, server: Any):
        ...

    def get_role(self, role: Union[str, int]) -> Optional[DummyRole]:
        return self.guilds[0]._roles[role]

    def get_channel(self, channel_id: int) -> None:
        ...

    async def fetch_user(self, ucid: str) -> Optional[DummyMember]:
        return await self.guilds[0].fetch_member(ucid)