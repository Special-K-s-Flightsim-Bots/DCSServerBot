import asyncio
import importlib
import importlib.util

from core import NodeImpl, ServiceRegistry, EventListener, Server, Plugin, PluginError
from core.utils.audit import record_audit
from typing import Any, Iterable

from services.bot.dummy import DummyGuild, DummyMember, DummyRole


class DummyIntents:
    """Every intent reported as disabled: there is no gateway to receive events from."""

    def __getattr__(self, item: str) -> bool:
        return False


class DummyCommandTree:
    """The command registry discord.py keeps, without a Discord to sync it to."""

    def __init__(self):
        self._commands: dict[str, Any] = {}

    def add_command(self, command: Any, /, **kwargs) -> None:
        name = getattr(command, 'name', None)
        if name:
            self._commands[name] = command

    def remove_command(self, name: str, /, **kwargs) -> Any:
        return self._commands.pop(name, None)

    def get_command(self, command: Any, /) -> Any:
        name = command if isinstance(command, str) else getattr(command, 'name', None)
        return self._commands.get(name) if name else None

    async def fetch_commands(self, *, guild: Any = None, **kwargs) -> list[Any]:
        """Nothing was ever synced to Discord, so there is nothing to list."""
        return []


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
        self.user = self.member
        self.audit_channel = None
        self.intents = DummyIntents()
        self.tree = DummyCommandTree()

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

    @property
    def extensions(self) -> dict[str, Any]:
        """The loaded plugins under the names the Discord bot gives them.

        A caller that asks whether an extension is loaded — the console's apply door asks exactly
        that before it reloads anything — gets the same answer on a headless install.
        """
        return {f"plugins.{name.lower()}.commands": cog for name, cog in self.cogs.items()}

    async def unload_plugin(self, plugin: str) -> bool:
        """Unload ONE plugin: its cog's own ``cog_unload``, then forget it."""
        for name in [name for name in self.cogs if name.lower() == plugin.lower()]:
            await self.cogs.pop(name).cog_unload()
            return True
        return False

    async def reload_plugin(self, plugin: str) -> bool:
        """Unload and load ONE plugin, the way the Discord bot does."""
        if await self.unload_plugin(plugin):
            return await self.load_plugin(plugin.lower())
        return False

    async def reload(self, plugin: str | None = None) -> bool:
        """Reload one plugin, or every loaded one when no name is given."""
        if plugin:
            return await self.reload_plugin(plugin)
        results = [await self.reload_plugin(name) for name in list(self.cogs)]
        return all(results) if results else True

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        for plugin in self.plugins:
            if not await self.load_plugin(plugin.lower()):
                self.log.info(f'  => {plugin.title()} NOT loaded.')
        # let the plugins register their admin web UI pages (no-op when no WebService runs on
        # this node). This mirrors where the Discord plugins are loaded: after the cogs, from the
        # same list, so a page and a command of one plugin can never disagree about loading.
        from core.plugin_manager import PluginManager
        self.plugin_manager = PluginManager(self.node)
        await self.plugin_manager.load_plugins(self.plugins)
        # cleanup remote servers (if any)
        for key in [key for key, value in self.bus.servers.items() if value.is_remote]:
            self.bus.servers.pop(key)
        self.setup.set()

    async def audit(self, message, *, user: Any = None, server: Server | None = None,
                    node: NodeImpl | None = None, mention: Any = None,
                    actor: str | None = None, **kwargs) -> None:
        """Record ONE entry in the cluster's audit table.

        This is the whole of the audit on an install without Discord: there is no channel to
        announce it in, so only the ledger row is written. ``mention``, ``server``'s channel and the
        embed fields exist for the Discord bot and have no counterpart here.
        """
        discord_id: int | None = None
        ucid: str | None = None
        if isinstance(user, int):
            discord_id = user
        elif isinstance(user, str):
            ucid = user
        elif user is not None:
            # a DummyMember carries a ucid as its id, a numeric id is a Discord id
            member_id = getattr(user, 'id', None)
            if isinstance(member_id, int):
                discord_id = member_id
            elif member_id:
                ucid = str(member_id)
        await record_audit(
            self.apool, node=(node or self.node).name, event=message,
            server_name=server.name if server else None,
            actor=actor, discord_id=discord_id, ucid=ucid)

    def get_admin_channel(self, server: Server) -> None:
        ...

    async def get_member(self, user_id: int, fetch: bool = True) -> DummyMember | None:
        """Resolve ONE member by Discord id.

        A headless install has no Discord to ask, so the only members that can be resolved are
        the ones a role mapping in ``bot.yaml`` already names.
        """
        if user_id in (-1, None):
            return None
        try:
            return self.guilds[0].get_member(int(user_id))
        except (TypeError, ValueError):
            return None

    async def get_members(self, user_ids: Iterable[int], fetch: bool = True,
                          concurrency: int = 5, budget: int | None = None) -> dict[int, DummyMember]:
        """Resolve several members at once, from the local role map only.

        Mirrors the Discord bot's contract: the answer is keyed by the numeric id and holds just
        the ids that resolved, so a caller falls back to the stored name for the rest.
        """
        members: dict[int, DummyMember] = {}
        for user_id in user_ids:
            try:
                _id = int(user_id) if user_id is not None else 0
            except (TypeError, ValueError):
                continue
            if _id <= 0 or _id in members:
                continue
            if (member := self.guilds[0].get_member(_id)) is not None:
                members[_id] = member
        return members

    async def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> DummyMember | None:
        return await self.get_member_by_ucid(ucid, verified)

    async def get_ucid_by_member(self, member: DummyMember, _verified: bool | None = False) -> str:
        return member.id

    async def get_member_by_ucid(self, ucid: str, _verified: bool | None = False) -> DummyMember | None:
        return self.guilds[0].get_member(ucid)

    async def match_user(self, data: dict, rematch=False) -> None:
        ...

    def get_server(self, ctx: Any, *, admin_only: bool | None = False) -> None:
        ...

    def get_servers(self, manager: Any = None) -> dict[str, Server]:
        """Every server of this node; there are no role-based scopes without Discord."""
        return dict(self.servers)

    async def setEmbed(self, *, embed_name: str, embed: Any, channel_id: Any, file: Any, server: Any):
        ...

    def get_role(self, role: str | int) -> DummyRole | None:
        return self.guilds[0]._roles[role]

    def get_channel(self, channel_id: int) -> None:
        ...

    def get_all_channels(self) -> Iterable[Any]:
        """No channels exist without Discord."""
        return iter(())

    def get_all_members(self) -> Iterable[DummyMember]:
        """The members a role mapping in ``bot.yaml`` names; there is no member cache otherwise."""
        return iter(self.guilds[0].members)

    def get_user(self, user_id: int) -> DummyMember | None:
        """Cache-only lookup: without Discord there is nobody to ask for an unknown id."""
        try:
            return self.guilds[0].get_member(int(user_id))
        except (TypeError, ValueError):
            return None

    def get_cog(self, name: str, default: Any = None) -> Plugin | Any:
        """A loaded plugin by its class name, the way discord.py's ``Bot.get_cog`` answers."""
        return self.cogs.get(name, default)

    def check_channel(self, channel_id: int, permissions: Iterable[str] | None = None) -> bool:
        """There are no channels and no permissions to verify without Discord."""
        return True

    def mention_admin(self, server: Server | None = None) -> str:
        """Nobody to mention: the answer is the empty mention string, never ``None``."""
        return ''

    async def fetch_embed(self, embed_name: str, channel: Any, server: Server | None = None) -> None:
        """No persisted message to fetch: a caller renders a fresh embed."""
        return None

    async def change_presence(self, **kwargs) -> None:
        """No presence to set without a gateway connection."""
        ...

    async def fetch_user(self, ucid: str) -> DummyMember | None:
        return await self.guilds[0].fetch_member(ucid)

    def add_command(self, command: Any, /) -> None:
        ...

    def remove_command(self, name: str, /) -> None:
        ...

    async def fetch_channel(self, channel_id: int, /) -> None:
        ...
