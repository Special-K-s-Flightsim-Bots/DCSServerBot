# DCSServerBot Core Framework Reference

This document describes the core framework classes and APIs available to plugin, extension, and service developers.

## Table of Contents
1. [Plugin System](#plugin-system)
2. [Extension System](#extension-system)
3. [Service System](#service-system)
4. [Event System](#event-system)
5. [Data Layer](#data-layer)
6. [Database](#database)
7. [Utilities](#utilities)
8. [Report Framework](#report-framework)

---

## Plugin System

### Class: `core.plugin.Plugin`

Base class for all plugins. Inherits from `discord.ext.commands.Cog`.

**Key attributes:**
- `self.bot` — `DCSServerBot` instance
- `self.node` — `NodeImpl` (the node this plugin runs on)
- `self.apool` — async database pool (`psycopg`, default)
- `self.pool` — sync database pool (to be used in non-async code only)
- `self.loop` — asyncio event loop
- `self.log` — `logging.Logger`
- `self.locals` — plugin config dict (from YAML)
- `self.eventlistener` — optional `EventListener` instance
- `self.plugin_name` — plugin name string
- `self.plugin_version` — version string

**Key methods:**
- `get_config(server=None, plugin_name=None, use_cache=True) -> dict` — read config (DEFAULT merged with server-specific)
- `install() -> bool` — called on plugin load (creates DB tables, etc.)
- `migrate(new_version, conn)` — called on version upgrade
- `read_locals() -> dict` — read plugin YAML config from `config/plugins/<plugin>.yaml`
- `before_dcs_update()` / `after_dcs_update()` — hooks for DCS update events
- `prune(conn, days)` — called during database pruning

**Lifecycle:** `install()` → `cog_load()` (registers event listener) → `cog_unload()` (shuts down)

### Class: `core.listener.EventListener`

Base class for DCS event handlers and in-game chat commands.

**Event handling:** Implement callbacks by using `@event`:
```python
from core import EventListener, event, Server

class MyListener(EventListener):
    @event(name="onMissionStart")
    async def onMissionEnd(self, server: Server, data: dict): ...
    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict): ...
    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict): ...
```

**Chat Commands:** Use `@chat_command` to handle `-command` in DCS chat:
```python
from core import EventListener, chat_command, Server, Player

class MyListener(EventListener):
    @chat_command(name="info", help="Show info")
    async def info(self, server: Server, player: Player, params: list[str]):
        await player.sendChatMessage("Hello!")
```

**Available events:** `onMissionStart`, `onMissionEnd`, `onPlayerJoin`, `onPlayerLeave`, `onChat`, `onKill`, `onHit`, `onTakeoff`, `onLanding`, `onCrash`, `onEjection`, `onRefueling`, `onDedicatedServerMessage`, `onSelfKill`, `onBirth`, `onPause`, `onContinue`, `onSlotChange`, `onRunwayTakeoff`, `onRunwayLanding`, `onEvent`

### Discord Commands

Plugins define slash commands using the @command() decorator. 
Note that server and node parameters are auto-injected based on the channel context:
```python
import discord
from core import command, utils, Plugin, Server, Status
from discord import app_commands


class MyPlugin(Plugin):
    @command(name="ping", description="Send a ping")
    async def ping(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        await interaction.response.send_message(f"Pong: {server.name}")
```

You can group commands by creating a Group:
```python
import discord
from core import Group, Plugin


class MyPlugin(Plugin):
    mygroup = Group(name='mygroup', description='My group commands')

    @mygroup.command(name="test", description='Test command')
    async def test(self, interaction: discord.Interaction):
        await interaction.response.send_message('Test command executed')
```

Commands are auto-registered when the cog loads.

### Internationalization

To support multiple languages, plugins can use the `get_translation()` function to retrieve the respective translation
from associated po / mo files.
These files are located in the `locales` directory:

```
|_ locales
    |_ de
        |_ LC_MESSAGES
            |_ myplugin.po
    |_ en
        |_ LC_MESSAGES
            |_ myplugin.po
```

You can generate the necessary mo files with gettext: `msgfmt -o .\myplugin.mo .\myplugin.po`.
After that, you can use the translation function as follows:

```python
import discord
from core import get_translation, Group, Plugin

_ = get_translation(__name__.split('.')[1])     # i18n


class MyPlugin(Plugin):
    mygroup = Group(name='mygroup', description=_('My group commands'))

    @mygroup.command(name="test", description=_('Test command'))
    async def test(self, interaction: discord.Interaction):
        await interaction.response.send_message(_('Test command executed'))
```

---

## Extension System

### Class: `core.extension.Extension`

Base class for extensions (third-party integrations like Tacview, SRS).

**Key attributes:**
- `self.node` — the `NodeImpl` this extension runs on
- `self.log` — logger
- `self.pool` — database pool
- `self.loop` — event loop
- `self.lock` — asyncio lock

**Lifecycle methods (override these):**
- `async prepare() -> bool` — called before DCS server starts
- `async startup() -> bool` — called after DCS server starts
- `shutdown() -> bool` — called on server stop
- `is_running() -> bool` — return current running state
- `async render(param=None) -> dict` — return status dict for display
- `async beforeMissionLoad(filename: str) -> tuple[str, bool]` — modify mission files before load

**Scheduling:** Use `@tasks.loop` decorator for periodic tasks:
```python
from discord.ext import tasks

@tasks.loop(hours=24.0)
async def schedule(self):
    pass
```

**Config:** Extensions are configured in `config/nodes.yaml` under the `extensions:` key at node and/or instance level.

---

## Service System

### Class: `core.services.base.Service`

Base class for shared services. Register using `@ServiceRegistry.register()`.

**Registration options:**
- `plugin` — only load if this plugin is enabled
- `master_only` — only run on master node
- `agent_only` — only run on agent nodes
- `depends_on` — list of services this service depends on

**Key methods:**
- `async start()` — called on service start
- `async stop()` — called on service stop
- `async switch(master: bool)` — called on master/agent failover
- `get_config(server=None) -> dict` — read service YAML config

### Class: `core.services.registry.ServiceRegistry`

Singleton registry for looking up services:
```python
from core import ServiceRegistry
from services.bot import BotService

bot_service = ServiceRegistry.get(BotService)
```

### Remote Calls: `@proxy` Decorator

Methods decorated with `@proxy` automatically route to the correct node, if a server or node is passed as an argument:
```python
from core import proxy, Server, Service, ServiceRegistry
from services.bot import BotService

@ServiceRegistry.register(plugin="myplugin", master_only=True, depends_on=[BotService])
class MyService(Service):
    @proxy
    async def my_method(self, server: Server):
        # If server is remote, this runs on the remote node
        pass
```

---

## Data Layer

### Class: `core.data.server.Server`

Represents a DCS server instance. Key properties / methods:
- `name` — server name
- `status` — `Status` enum (LOADING, RUNNING, PAUSED, STOPPED, etc.)
- `instance` — the `Instance` this server runs in
- `node` — the `Node` this server is on
- `players` — dict of connected Player objects by UCID
- `settings / options` — access to serverSettings.lua and options.lua
- `send_to_dcs(message: dict)` — async fire-and-forget to DCS
- `send_to_dcs_sync(message: dict)` -> dict — async call returning DCS response
- `sendChatMessage(coalition, message)` / `sendPopupMessage(recipient, message)`

### Class: `core.data.node.Node`

Represents a node (a DCSServerBot instance). Key properties:
- `name` — node name
- `log` — logger
- `config_dir` — where config files are stored
- `master` — boolean (is master node)
- `all_nodes` — dict of all nodes in the cluster
- `instances` — list of `Instance` objects
- `locals` — internal nodes.yaml representation
- `config` — internal main.yaml representation

When accessing the local node, you will retrieve a `core.data.node.NodeImpl` instance.
This class is a subclass of `core.data.node.Node` and adds additional properties and methods:
- `pool` — the synchronous database pool (avoid using this)
- `apool` — the asynchronous database pool (default)
- `cpool` — the cluster pool (only distinct from apool on `Federation`)

### Class: `core.data.player.Player`

Represents a connected player. Key properties / methods:
- `ucid`, `name`, `side`, `slot`, `unit_type`, `unit_name`
- `member` — associated `discord.Member` (if linked)
- `verified` — boolean if account link is manual/verified
- `sendChatMessage(message)` / `sendPopupMessage(message)`

### Class: `core.data.instance.Instance`

Represents a DCS instance (executable). Key properties:
- `name` — instance name
- `node` — the `Node` this instance is on
- `server` — the `Server` this instance is running
- `missions_dir` — the directory where missions are stored

### Enums: `core.const`

- `Status` — LOADING, RUNNING, PAUSED, STOPPED, SHUTTING_DOWN
- `ChannelType` — ADMIN, STATUS, etc.

---

## Database

All database access should use `psycopg`'s async pool (`self.apool`).

**Pattern:**
```python
async with self.apool.connection() as conn:
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM my_table WHERE id = %s", (id,))
        row = await cur.fetchone()
```

**Plugin tables:** Define DDL in `db/tables.sql` — auto-executed on plugin install.
**Migrations:** Versioned SQL files in `db/update_vX.Y.sql`.

---

## Utilities

### `core.utils`

Common helper functions:
- `utils.dynamic_import(module_path)` — dynamic module import
- `utils.cmd_has_roles(roles)` — Discord command check
- `utils.deep_merge(dict1, dict2)` — merge configs
- `utils.escape_string(str)` — escape DCS special characters
- `utils.check_roles(roles, member)` — permission checks

---

## Report Framework

Reports are defined in `plugins/<plugin>/reports/<report>.json`. 
They use a structured JSON format to define headers, tables, charts, and maps, with data provided via ReportEnv.
See [here](../reports/README.md) for full documentation.
