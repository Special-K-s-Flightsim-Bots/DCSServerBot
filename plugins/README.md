# DCSServerBot Plugin Development Guide

DCSServerBot is built on a modular plugin architecture. 
Plugins extend the bot's capabilities across Discord slash commands, in-game chat commands, DCS World event hooks, 
mission scripting, and relational database storage.

---

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Plugin Directory Structure](#plugin-directory-structure)
3. [Quick Start: Hello World Plugin](#quick-start-hello-world-plugin)
4. [Enabling Plugins (`opt_plugins` in `main.yaml`)](#enabling-plugins-opt_plugins-in-mainyaml)
5. [Configuration System](#configuration-system)
6. [Core Classes & Reference](#core-classes--reference)
   - [Plugin Class (`commands.py`)](#plugin-class-commandspy)
   - [EventListener Class (`listener.py`)](#eventlistener-class-listenerpy)
   - [DCS Hook Callbacks (`lua/callbacks.lua`)](#dcs-hook-callbacks-luacallbackslua)
   - [DCS Hook Commands (`lua/commands.lua`)](#dcs-hook-commands-luacommandslua)
   - [Mission Scripting Environment (`lua/mission.lua`)](#mission-scripting-environment-luamissionlua)
7. [DCSServerBot Data Classes](#dcsserverbot-data-classes)
   - [Server](#server)
   - [Player](#player)
   - [Instance](#instance)
   - [Mission](#mission)
   - [discord.Member](#discordmember)
8. [Database Integration & Schema](#database-integration--schema)
9. [Migrations & Versioning](#migrations--versioning)
10. [Third-Party Dependencies](#third-party-dependencies)
11. [Reports Framework Integration](#reports-framework-integration)
12. [Best Practices & Checklist](#best-practices--checklist)

---

## Architecture Overview

A plugin in DCSServerBot connects three execution environments:
1. **Discord Bot (Python / discord.py / asyncio)**: Hosts slash commands (`commands.py`), listens to DCS events (`listener.py`), and manages database / configuration operations.
2. **DCS World Hook Environment (Lua / GUI/Hook context)**: Runs inside DCS World `Scripts/Hooks/` to intercept server lifecycle and player events (`lua/callbacks.lua`) or execute bot-dispatched actions (`lua/commands.lua`).
3. **DCS Mission Scripting Environment (MSE / Lua)**: In-mission scripting loaded inside the mission runtime (`lua/mission.lua`).

Communication flow:
- **Discord -> DCS**: `server.send_to_dcs()` (async UDP) or `server.send_to_dcs_sync()` (RPC waiting for response).
- **DCS -> Discord**: `utils.sendBotTable(msg, channel)` in Lua triggers registered `@event` handlers in `listener.py`.
- **In-Game Chat -> Discord**: Player types `.command` in DCS chat, intercepted by the hook and routed to `@chat_command` handlers in `listener.py`.

---

## Plugin Directory Structure

Each plugin resides in its own folder under `plugins/<plugin_name>/`.

```text
plugins/<plugin_name>/
├── __init__.py            # Package initialization, exports version
├── version.py             # Plugin semantic version string
├── commands.py            # Main Plugin cog with Discord slash commands
├── listener.py            # EventListener for DCS events and in-game chat commands (optional)
├── README.md              # Plugin documentation, commands, and YAML config schema
├── db/                    # Database definitions and migration scripts (optional)
│   ├── tables.sql         # DDL statements for creating plugin tables
│   └── update_vX.Y.sql    # Migration DDL from version X.Y to next version
├── lua/                   # DCS Lua scripts (optional)
│   ├── callbacks.lua      # DCS user callbacks (Sim.setUserCallbacks)
│   ├── commands.lua       # Hook-level command handlers (dcsbot.<name>)
│   └── mission.lua        # Mission Scripting Environment code (loaded into mission)
└── reports/               # Custom report templates (copied to /reports/<plugin_name>) (optional)
```

### Key File Roles
| File / Directory    | Mandatory  | Description                                                              |
|---------------------|------------|--------------------------------------------------------------------------|
| `version.py`        | **Yes**    | Defines `__version__ = "X.Y"`                                            |
| `__init__.py`       | **Yes**    | Imports version: `from .version import __version__`                      |
| `commands.py`       | **Yes**    | Implements the `Plugin` class and the `async def setup(bot)` entry point |
| `listener.py`       | No         | Implements the `EventListener` class for DCS events / in-game chat       |
| `README.md`         | **Yes**    | Human- and AI-readable documentation for the plugin                      |
| `db/tables.sql`     | No         | Initial PostgreSQL table schema for the plugin                           |
| `db/update_v*.sql`  | No         | Incremental SQL migration scripts executed on version bumps              |
| `lua/callbacks.lua` | No         | DCS World GUI/Hook callbacks                                             |
| `lua/commands.lua`  | No         | DCS World Hook command handlers invoked by Python RPC                    |
| `lua/mission.lua`   | No         | Lua code intended to run inside DCS Mission Scripting Environment        |
| `reports/`          | No         | JSON/YAML templates for DCSServerBot reporting framework                 |

---

## Quick Start: Hello World Plugin

Here is a minimal, complete plugin template.

### 1. `version.py`
```python
__version__ = "1.0.0"
```

### 2. `__init__.py`
```python
from .version import __version__

__all__ = ["__version__"]
```

### 3. `commands.py`
```python
import discord
from discord import app_commands
from core import Plugin, command, utils, Server, Status
from services.bot import DCSServerBot
from typing import Type

from .listener import HelloWorldListener


class HelloWorld(Plugin[HelloWorldListener]):

    @command(description="Send a message to DCS and get an echo response.")
    @app_commands.guild_only()
    @utils.app_has_role("DCS")
    async def echo(
        self,
        interaction: discord.Interaction,
        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
        message: str
    ):
        await interaction.response.defer(thinking=True)
        # Send synchronous request to DCS commands.lua
        response = await server.send_to_dcs_sync({
            "command": "echoMessage",
            "message": message
        })
        await interaction.followup.send(f"DCS echoed: {response.get('result', 'No response')}")


async def setup(bot: DCSServerBot):
    await bot.add_cog(HelloWorld(bot, HelloWorldListener))
```

### 4. `listener.py`
```python
from core import EventListener, Server, Player, event, chat_command
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import HelloWorld


class HelloWorldListener(EventListener["HelloWorld"]):

    @event(name="registerDCSServer")
    async def on_register(self, server: Server, data: dict) -> None:
        self.log.info(f"HelloWorld: Server {server.name} registered.")

    @chat_command(name="hello", roles=["DCS Admin"], help="Greets the user in DCS chat")
    async def hello_chat(self, server: Server, player: Player, params: list[str]) -> None:
        await player.sendChatMessage(f"Hello {player.name}, welcome to {server.name}!")
```

### 5. `lua/commands.lua`
```lua
local base = _G
local dcsbot = base.dcsbot
local utils = base.require("DCSServerBotUtils")

function dcsbot.echoMessage(json)
    log.write('DCSServerBot', log.DEBUG, 'HelloWorld: echoMessage() called with: ' .. tostring(json.message))
    local msg = {
        command = 'echoMessage',
        result = "Echo: " .. tostring(json.message)
    }
    utils.sendBotTable(msg, json.channel)
end
```

### 6. Enable the Plugin in `config/main.yaml`
Add your new plugin folder name to `opt_plugins` in `config/main.yaml`:
```yaml
opt_plugins:
  - helloworld
```

---

## Enabling Plugins (`opt_plugins` in `main.yaml`)

DCSServerBot categorizes plugins into **default core plugins** and **optional/custom plugins**:

### Core Default Plugins vs Optional Plugins
- **Default Core Plugins**: Automatically loaded at bot startup without requiring entry in `config/main.yaml`:
  - `mission`
  - `scheduler`
  - `help`
  - `admin`
  - `userstats`
  - `missionstats`
  - `monitoring`
  - `gamemaster`
  - `creditsystem`
  - `cloud`
- **Optional & Custom Plugins**: Any additional built-in plugins (e.g. `serverstats`, `motd`, `greenieboard`, `punishment`, `slotblocking`, `music`, `funkman`, `modmanager`, `commands`, `restapi`) as well as **any new custom plugins you develop** must be registered under `opt_plugins` in `config/main.yaml`.

### Configuration in `config/main.yaml`

To enable your plugin, add its directory name to the `opt_plugins` list in `config/main.yaml`:

```yaml
# config/main.yaml
guild_id: 112233445566
chat_command_prefix: .

opt_plugins:
  - serverstats
  - motd
  - greenieboard
  - punishment
  - slotblocking
  - music
  - funkman
  - modmanager
  - commands
  - restapi
  - helloworld      # Your new custom plugin directory under ./plugins/helloworld
```

### What Happens During Plugin Initialization

When DCSServerBot starts up (`NodeImpl`):
1. **List Aggregation**: Merges default core plugins with all plugins listed in `opt_plugins`.
2. **Database Initialization & Migrations**: Checks for `db/tables.sql` (first install) or `db/update_v*.sql` / `migrate()` (version upgrades) and executes them.
3. **Report Templates**: Automatically copies JSON templates from `plugins/<plugin>/reports/` to `reports/<plugin>/`.
4. **DCS Hooks & Lua Scripts**: Installs hook callbacks (`callbacks.lua`) and command handlers (`commands.lua`) to the DCS World hook directory.
5. **Discord Cog Registration**: Loads the plugin cog (`plugins.<plugin>.commands`) into the Discord bot and registers slash commands.
6. **EventListener Registration**: Binds `@event` and `@chat_command` listeners to the `ServiceBus`.

### Dynamic Management via Discord Commands

Administrators can also add or remove optional plugins dynamically at runtime without manually editing YAML:
- `/plugin install <plugin_name>`: Automatically adds the plugin to `opt_plugins` in `config/main.yaml`, runs installation routines, and loads the extension.
- `/plugin uninstall <plugin_name>`: Unloads the plugin and removes it from `opt_plugins` in `config/main.yaml`.

---

## Configuration System

Plugins can use YAML files in `config/plugins/<plugin_name>.yaml` for configuration.

### YAML Structure & Inheritance
```yaml
# config/plugins/sample.yaml
DEFAULT:
  greeting: "Welcome to the server!"
  kick_penalty: 10
  audit_channel: 123456789012345678

# Instance- or server-specific override (instance name or server name)
DCS.dcs_serverrelease:
  greeting: "Welcome to Server 1!"
  kick_penalty: 20
```

### Reading Configuration in Python

#### Inside `Plugin`
```python
# Returns merged config for server (DEFAULT + server override)
config: dict = self.get_config(server)

# Returns only the DEFAULT section
default_config: dict = self.get_config()

# Returns another plugin's configuration for a server
admin_config: dict = self.get_config(server, plugin_name="Admin")
```

#### Inside `EventListener`
```python
# Access via self.plugin
config: dict = self.plugin.get_config(server)
```

> **Configuration Precedence:** When passing `server`, `get_config(server)` automatically merges `DEFAULT` values with server-specific overrides, giving priority to the server section.

### Overriding Discord Commands via YAML
Administrators can customize plugin slash commands directly in `config/plugins/<plugin_name>.yaml`:
```yaml
commands:
  echo:
    name: "server-echo"             # Rename command
    description: "New description"  # Custom description
    roles: ["DCS Admin", "Moderator"] # Restrict to roles
    enabled: true                   # Set false to disable command
```

---

## Core Classes & Reference

### Plugin Class (`commands.py`)

Inherits from `discord.ext.commands.Cog` and `typing.Generic[TEventListener]`.

```python
from core import Plugin, TEventListener
from services.bot import DCSServerBot
from typing import Type, Optional
import psycopg

class MyPlugin(Plugin[MyEventListener]):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
```

#### Available Attributes
| Attribute             | Type                        | Description                                              |
|-----------------------|-----------------------------|----------------------------------------------------------|
| `self.bot`            | `DCSServerBot`              | The Discord bot instance                                 |
| `self.node`           | `Node`                      | Current node instance (master or agent)                  |
| `self.bus`            | `ServiceBus`                | Inter-process / inter-node messaging bus                 |
| `self.log`            | `logging.Logger`            | Scoped logger (`plugins.<name>.<Class>`)                 |
| `self.apool`          | `AsyncConnectionPool`       | PostgreSQL async connection pool (preferred)             |
| `self.pool`           | `ConnectionPool`            | PostgreSQL sync connection pool (legacy / blocking only) |
| `self.loop`           | `asyncio.AbstractEventLoop` | Asyncio event loop                                       |
| `self.locals`         | `dict`                      | Raw parsed contents of `config/plugins/<name>.yaml`      |
| `self.plugin_name`    | `str`                       | Name of the plugin directory                             |
| `self.plugin_version` | `str`                       | Version from `version.py`                                |
| `self.eventlistener`  | `Optional[TEventListener]`  | Bound `EventListener` instance                           |

#### Lifecycle Methods
- `async def cog_load(self) -> None`: Called when the plugin cog is loaded. Initializes DB, creates report directories, and registers event listeners with the ServiceBus. Always call `await super().cog_load()`.
- `async def cog_unload(self) -> None`: Called on plugin unload. Shuts down listeners and unregisters them. Always call `await super().cog_unload()`.
- `async def install(self) -> bool`: Executes `tables.sql` on first install.
- `async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None`: Called during automated plugin migrations.
- `async def before_dcs_update(self) -> None`: Hook executed before a DCS World update is triggered.
- `async def after_dcs_update(self) -> None`: Hook executed after a DCS World update completes, before servers restart.
- `async def prune(self, conn: psycopg.AsyncConnection, days: int) -> None`: Database cleanup callback invoked when pruning historical data.
- `async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None`: Invoked when a player's UCID changes.

#### Slash Command Decorators & Checks
- `@command(description="...")`: Defines a Discord application slash command.
- `@app_commands.guild_only()`: Restricts command to Discord guilds (no DMs).
- `@utils.app_has_role("RoleName")`: Checks if the invoking user has a specific Discord role.
- `@utils.app_has_roles(["Role1", "Role2"])`: Checks if the user has any of the listed roles.
- `@utils.app_has_not_role("RoleName")`: Checks if the user does not have a specific role.
- `app_commands.Transform[Server, utils.ServerTransformer(status=[...])]`: Automatically resolves Discord selection to a `Server` instance filtered by server status (`Status.RUNNING`, `Status.PAUSED`, `Status.STOPPED`, etc.).

---

### EventListener Class (`listener.py`)

Inherits from `core.EventListener[TPlugin]`. Listens to DCS World events and handles in-game chat commands.

```python
from core import EventListener, Server, Player, event, chat_command
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import MyPlugin


class MyEventListener(EventListener["MyPlugin"]):
    pass
```

#### Available Attributes
| Attribute          | Type                  | Description                             |
|--------------------|-----------------------|-----------------------------------------|
| `self.plugin`      | `TPlugin`             | The parent `Plugin` instance            |
| `self.plugin_name` | `str`                 | Plugin name string                      |
| `self.bot`         | `DCSServerBot`        | The Discord bot instance                |
| `self.log`         | `logging.Logger`      | Scoped logger                           |
| `self.apool`       | `AsyncConnectionPool` | PostgreSQL async connection pool        |
| `self.locals`      | `dict`                | Configuration dictionary from YAML      |
| `self.prefix`      | `str`                 | In-game chat command prefix (e.g., `.`) |

#### Built-in Hook Events (`@event`)
Decorate listener methods with `@event(name="<eventName>")`:
```python
@event(name="onPlayerConnect")
async def onPlayerConnect(self, server: Server, data: dict) -> None:
    # data contains: id, name, ucid, side, etc.
    self.log.info(f"Player {data.get('name')} connected to {server.name}")
```

Standard DCS lifecycle & game events:
- `registerDCSServer`: Dispatched when a DCS server initializes and registers with the bot.
- `onMissionLoadBegin`: DCS begins loading a mission.
- `onMissionLoadEnd`: Mission loading completed.
- `onSimulationStart`: Simulation engine started.
- `onSimulationStop`: Simulation engine stopped.
- `onSimulationPause`: Simulation paused.
- `onSimulationResume`: Simulation unpaused.
- `onPlayerConnect`: Player initiated connection (`id`, `name`, `ucid`).
- `onPlayerStart`: Player entered the world.
- `onPlayerStop`: Player left the unit/aircraft.
- `onPlayerChangeSlot`: Player switched slots (`side`, `unit_type`, `slot`).
- `onGameEvent`: In-game engine events (kills, crashes, ejections, hits, takeoffs, landings).
- `onChatMessage`: In-game chat message received.

#### In-Game Chat Commands (`@chat_command`)
```python
@chat_command(
    name="mycommand",
    aliases=["mc", "mycmd"],
    roles=["DCS Admin", "GameMaster"],
    help="Brief explanation shown in .help",
    usage="<required_arg> [optional_arg]",
    hidden=False,
    enabled=True
)
async def mycommand(self, server: Server, player: Player, params: list[str]) -> None:
    if not params:
        await player.sendChatMessage(f"Usage: {self.prefix}mycommand <arg>")
        return
    await player.sendChatMessage(f"Received argument: {params[0]}")
```

---

### DCS Hook Callbacks (`lua/callbacks.lua`)

Runs in the DCS GUI/Hook Lua environment (`Scripts/Hooks`). Uses `Sim.setUserCallbacks()`.

```lua
local base   = _G
local utils  = base.require("DCSServerBotUtils")
local config = base.require("DCSServerBotConfig")

local myplugin = {}

function myplugin.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'MyPlugin: onMissionLoadEnd()')
    -- Optionally inject mission.lua into Mission Scripting Environment
    local script = 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/myplugin/mission.lua' .. '\\")")'
    net.dostring_in('mission', script)
end

function myplugin.onPlayerConnect(id)
    local msg = {
        command = 'playerJoinedCustom',
        id = id,
        name = net.get_player_info(id, 'name'),
        ucid = net.get_player_info(id, 'ucid')
    }
    utils.sendBotTable(msg, config.CHAT_CHANNEL)
end

Sim.setUserCallbacks(myplugin)
```

---

### DCS Hook Commands (`lua/commands.lua`)

Defines functions attached to `dcsbot.<command_name>` callable by Python via `server.send_to_dcs()` or `server.send_to_dcs_sync()`.

```lua
local base   = _G
local dcsbot = base.dcsbot
local utils  = base.require("DCSServerBotUtils")

function dcsbot.customServerAction(json)
    log.write('DCSServerBot', log.DEBUG, 'Custom action: ' .. tostring(json.action))
    
    -- Perform action in Hook environment
    local success = true
    
    -- Send response back to bot (required for synchronous calls)
    local response = {
        command = 'customServerAction',
        success = success,
        payload = "Action completed"
    }
    utils.sendBotTable(response, json.channel)
end
```

---

### Mission Scripting Environment (`lua/mission.lua`)

Loaded directly into the mission runtime (MSE). Can interact with in-game mission units, groups, and triggers.

```lua
-- Always check if DCSServerBot hook is active
if dcsbot then
    -- Available functions provided by DCSServerBot core in MSE:
    -- sendBotMessage(msg, channel)
    -- sendBotTable(tbl, channel)
    -- sendEmbed(title, description, img, fields, footer, channel)
    -- updateEmbed(id, title, description, img, fields, footer, channel)
    -- callback(msg, channel)
    -- startMission(id)
    -- restartMission()
    -- disableUserStats()
    
    sendBotMessage("Mission script initialized successfully!")
end
```

---

## DCSServerBot Data Classes

DCSServerBot provides high-level data classes representing DCS entities across nodes.

### Server
Represents a DCS server instance.

```python
from core import Server

server: Server

# Properties
server.name             # Server name (str)
server.status           # Current Status (Status.RUNNING, Status.PAUSED, etc.)
server.current_mission  # Active Mission instance
server.instance         # Bound Instance object
server.node             # Node where server is hosted

# Communication
await server.send_to_dcs({"command": "myCmd"})                     # Asynchronous send
result = await server.send_to_dcs_sync({"command": "myCmd"})        # Synchronous RPC

# Lifecycle control
await server.start()
await server.stop()
await server.restart()

# Player lookup
player = server.get_player(ucid="...")
player = server.get_player(discord_id=123456789)
player = server.get_player(id=1)
player = server.get_player(name="PlayerName")
```

### Player
Represents an active or historic player.

```python
from core import Player

player: Player

# Properties
player.ucid             # DCS Unique Client ID (str)
player.id               # In-game session ID (int)
player.name             # In-game name (str)
player.side             # Coalition (Side.RED, Side.BLUE, Side.SPECTATOR, Side.NEUTRAL)
player.unit_type        # DCS unit typename (str, e.g. "FA-18C_hornet")
player.slot             # Slot ID (str / int)
player.member           # Linked discord.Member instance (or None if unlinked)
player.server           # Server instance player is on

# Player actions
await player.sendChatMessage("Hello in chat!")
await player.sendPopupMessage("Popup alert on screen", timeout=10)
await player.server.kick(player=player, reason="Violation of server rules")
```

### Instance
Represents the local DCS installation / instance directory.

```python
from core import Instance

instance: Instance = server.instance
instance.name           # Instance name (str)
instance.node           # Host Node
instance.home           # Path to Saved Games directory
```

### Mission
Represents the mission currently loaded on a server.

```python
from core import Mission

mission: Mission = server.current_mission
mission.name            # Mission file name / title
mission.map             # Map / Theatre (e.g. "Caucasus", "Persian Gulf")
await mission.pause()   # Pause simulation
await mission.unpause() # Unpause simulation
await mission.restart() # Restart mission
```

### discord.Member
Link between DCS player and Discord member:
```python
if player.member:
    await player.member.send("Direct message from DCS bot!")
```

---

## Database Integration & Schema

DCSServerBot uses **PostgreSQL** with `psycopg` (v3).

### DDL Definition (`db/tables.sql`)
Place all DDL definitions in `db/tables.sql`.

```sql
CREATE TABLE IF NOT EXISTS sample_stats (
    player_ucid TEXT NOT NULL,
    server_name TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    kills INTEGER NOT NULL DEFAULT 0,
    last_seen TIMESTAMP NOT NULL DEFAULT TIMEZONE('UTC', NOW()),
    PRIMARY KEY (player_ucid, server_name),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
```

#### Database Foreign Key Conventions
Always reference master tables using appropriate constraints:
- `players (ucid)` -> `ON UPDATE CASCADE ON DELETE CASCADE`
- `servers (server_name)` -> `ON UPDATE CASCADE ON DELETE CASCADE`
- `missions (id)` -> `ON DELETE CASCADE`
- `squadrons (id)` -> `ON DELETE CASCADE`
- `campaigns (id)` -> `ON DELETE CASCADE`

### Querying the Database in Python
Always prefer `self.apool` (asynchronous pool):

```python
# Async query execution with transaction
async with self.apool.connection() as conn:
    async with conn.transaction():
        await conn.execute("""
            INSERT INTO sample_stats (player_ucid, server_name, score)
            VALUES (%s, %s, %s)
            ON CONFLICT (player_ucid, server_name) DO UPDATE
            SET score = sample_stats.score + EXCLUDED.score,
                last_seen = TIMEZONE('UTC', NOW())
        """, (player.ucid, server.name, 10))

# Fetching rows
async with self.apool.connection() as conn:
    async with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        await cursor.execute("""
            SELECT player_ucid, score FROM sample_stats
            WHERE server_name = %s ORDER BY score DESC LIMIT 10
        """, (server.name,))
        top_players = await cursor.fetchall()
```

---

## Migrations & Versioning

### 1. `version.py`
Change the version whenever database schema or configuration structures change:
```python
__version__ = "1.0"
```

### 2. SQL Incremental Migration (`db/update_vX.Y.sql`)
The migration filename must match the version you are migrating **FROM**:
- To migrate from `1.0` to `1.1`, create `db/update_v1.0.sql`:
```sql
ALTER TABLE sample_stats ADD COLUMN assists INTEGER NOT NULL DEFAULT 0;
```

### 3. Programmatic Migration (`Plugin.migrate`)
Override `migrate()` in `commands.py` for complex migrations (e.g. updating YAML configs, recalculating statistics):

```python
import psycopg

from core import Plugin
from typing import Optional


class MyPlugin(Plugin):
    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == "1.1":
            # Modify local configuration or data structures
            self.log.info("Migrating MyPlugin to 1.1...")
            self.read_locals()
```

---

## Third-Party Dependencies

If your custom plugin requires Python packages outside the standard bot dependencies:
1. Create or edit `requirements.local` at the root of the DCSServerBot repository:
   ```text
   aiohttp>=3.9.0
   numpy>=1.26.0
   ```
2. Install into the bot's virtual environment:
   - **Windows Command Prompt**:
     ```cmd
     %USERPROFILE%\.dcssb\Scripts\pip install -r requirements.local
     ```
   - **PowerShell**:
     ```powershell
     & "$env:USERPROFILE\.dcssb\Scripts\pip.exe" install -r requirements.local
     ```

---

## Reports Framework Integration

Custom reports placed in `plugins/<plugin_name>/reports/` are automatically copied to `/reports/<plugin_name>/` upon plugin initialization.

Refer to the [Report Framework Documentation](../reports/README.md) for pagination, embeds, SQL reporting elements, and graph generation.

---

## Best Practices & Checklist

When developing or reviewing a new plugin:

- [ ] **Enable in `main.yaml`**: Add your plugin name to `opt_plugins` in `config/main.yaml` (or activate it via `/node plugin add <name>`).
- [ ] **Async First**: Use `self.apool` and asynchronous calls everywhere. Avoid blocking I/O or `time.sleep()`.
- [ ] **Sync vs Async DCS RPC**: Use `server.send_to_dcs_sync()` only when you require an immediate return payload from Lua; otherwise use fire-and-forget `server.send_to_dcs()`.
- [ ] **Unique Callback Names**: Prefix Lua callback message commands with your plugin name (e.g., `myplugin_playerEvent`) to avoid collisions.
- [ ] **Safe MSE Loading**: Guard Mission Scripting Environment scripts with `if dcsbot then ... end`.
- [ ] **Multi-Node Awareness**: Do not assume DCS server instances run on the same physical machine as the Discord bot; use `Server` and `Node` methods rather than direct local filesystem paths for server state.
- [ ] **Database Integrity**: Always specify foreign keys with `ON UPDATE CASCADE ON DELETE CASCADE` on `ucid` and `server_name`.
- [ ] **Proper Deferral**: In Discord slash commands doing network/database operations, call `await interaction.response.defer()` early to prevent Discord 3-second interaction timeouts.
- [ ] **Documentation**: Maintain `README.md` in the plugin root detailing configuration keys and Discord commands.
