---
title: Write your own plugin
parent: Plugin System
nav_order: 999
---

# Write your own plugin

In case you want to write your own plugin, there is a sample in the plugins/samples subdirectory, that will guide you through the steps.
If you want your plugin to be added to the distribution, just contact me via the contact details below.

## Plugin Structure

```
|_ db               
   |_ tables.sql        => DDLs for plugin-specific tables
   |_ update_v1.0.sql   => Update script, only needed if database changes have to be made
|_ lua
   |_ commands.lua      => Commands to be provided in the Hook environment
   |_ callbacks.lua     => Usual Hook callbacks for DCS (aka onXXX())
   |_ mission.lua       => lua file to be loaded inside the mission (no auto loading!)
|_ reports              => Reports used by the plugin (see ReportFramwork below)
__init__.py             => Package definition (see below)
commands.py             => Contains all Discord commands
listener.py             => Event listener for DCS events
version.py              => Holds the plugins version
README.md               => Each plugin should have a documentation
```

## Configuration

Each plugin _can_ use a json file to keep its config parameters. There json files are stored in ./config,
and it is a good habit to provide a sample for it.
As each plugin might need a different configuration for each server and maybe some default configuration,
the layout of the config files is as follows:

```json
{
  "configs": [
    {
      "name": "I am the default section"
    },
    {
      "installation": "DCS.release_server",
      "name": "I am a server specific section"
    }
  ]
}
```

To access the configuration, you can use

```python
    config: dict = self.get_config(server)
```

in your Plugin implementation or 

```
    config: dict = self.plugin.get_config(server)
```

in your EventListener implementation.

{: .note }
> The default configuration will be merged with the respective server specific configuration, giving the
> server specific configuration the priority over the default. If you don't want it like that, you need to
> overwrite the `get_config()` method in your own plugin configuration (see [Punishment]).


## Classes

When implementing a plugin, there are some python classes that you need to know.

### Class: Plugin

Base class for all plugins. Needs to be implemented inside the commands.py file (see below).

You have access to the following class variables:
* self.plugin_name: Plugin name ("sample")
* self.plugin_version: Plugin version ("1.0")
* self.bot: Main DCSServerBot instance
* self.log: Logging
* self.pool: Database pool
* self.loop: asyncio event loop
* self.locals: dict from config.json
* self.eventlistener: the EventListener instance bound to this plugin (optional)

```python
from core import Plugin

class Sample(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        # do something when the plugin is initialized
    
    async def cog_load(self) -> None:
        await super().cog_load()
        # do something (asynchronous) when the plugin is loaded
        
    async def cog_unload(self):
        await super().cog_unload()
        # do something when the plugin is unloaded

    async def install(self):
        await super().install()
        # do something when the plugin is installed for the first (!) time

    def migrate(self, version: str) -> None:
        # do something when the plugin is migrated (see below)
        pass

    async def before_dcs_update(self) -> None:
        # do something before a DCS upgrade takes place
        pass

    async def after_dcs_update(self) -> None:
        # do something after a DCS upgrade took place and before the servers are started
        pass

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None) -> None:
        # cleanup (the database) with data older than days and/or for specific users (ucids) 
        pass

    def rename(self, old_name: str, new_name: str) -> None:
        # called when a server rename took place and to be used to update your database tables accordingly
        pass

```

None of these methods needs to be overloaded for a plugin to work.

### Class: EventListener

You have access to the following class variables:
* self.plugin: the Plugin implementation bound to this EventListener
* self.plugin_name: name of the plugin
* self.bot: Main DCSServerBot instance
* self.log: Logging
* self.pool: Database pool
* self.loop: asyncio event loop
* self.locals: dict from config.json

```python
from core import EventListener, Server, Plugin, Player, event, chat_command

class SampleEventListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        # do something when the listener is initialized
        
    async def shutdown(self):
        await super().shutdown()
        # do something when the plugin is shut down
        
    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # called, when a DCS server is found and initialized
        # dict contains a dictionary with a lot of server information, like name, mission, active players,
        # weather and whatnot.
        ...
    
    # The following callbacks are derived from the Hooks environment.
    # No "name" parameter means the event name is the method name.
    @event()
    async def onMissionLoadBegin(self, data: dict) -> None:
        ...

    @event()
    async def onMissionLoadEnd(self, data: dict) -> None:
        ...
    
    @event()
    async def onSimulationStart(self, data: dict) -> None:
        ...
    
    @event()
    async def onSimulationStop(self, data: dict) -> None:
        ...
    
    @event()
    async def onSimulationPause(self, data: dict) -> None:
        ...
    
    @event()
    async def onSimulationResume(self, data: dict) -> None:
        ...
    
    @event()
    async def onPlayerConnect(self, data: dict) -> None:
        ...
    
    @event()
    async def onPlayerStart(self, data: dict) -> None:
        ...
    
    @event()
    async def onPlayerStop(self, data: dict) -> None:
        ...
    
    @event()
    async def onPlayerChangeSlot(self, data: dict) -> None:
        ...
    
    @event()
    async def onGameEvent(self, data: dict) -> None:
        ...
    
    @event()
    async def onChatMessage(self, data: dict) -> None:
        ...

    @event(name="myCustomCommand")
    async def myCustomCommand(self, server: Server, data: dict) -> None:
        # any custom command, which can be sent from the lua environment by using
        # local msg = {}
        # msg.command = 'myCustomCommand'
        # msg.param = { ... } -- all that goes into the dict
        # dcsbot.sendBotTable(msg)
        ...

    @chat_command(name="atis", usage="<airport>", help="ATIS information")
    async def atis(self, server: Server, player: Player, params: list[str]) -> None:
        # can be used by everyone
        ...
    
    @chat_command(name="restart", usage="[time]", help="Restart the server in [time] seconds", roles=["DCS Admin"])
    async def restart(self, server: Server, player: Player, params: list[str]) -> None:
        # can be used by DCS Admin only
        ...
```

## Main Files

### commands.py

This is the entry point for any Discord command. For how to handle Discord commands, see [discord.py].

```python
import discord

from core import Plugin, utils, Server, Status
from discord import app_commands
from services import DCSServerBot

from .listener import SampleEventListener


class Sample(Plugin):
    @app_commands.command(description='Agent-command')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sample(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])], text: str):
        await interaction.response.send_message(f'Server {server.name} is {server.status.name}!')


async def setup(bot: DCSServerBot):
    await bot.add_cog(Sample(bot, SampleEventListener))
```

You don't necessarily need to implement the MASTER / AGENT construct, if you only have commands that are
to be run on agents.

### listener.py

This is just the implementation of the EventListener class (see above).

### callbacks.lua

Every plugin can have their own DCS World hook, that will be automatically added to the Scripts\Hooks
environment. To achieve this, you just need to place a file named callbacks.lua in your lua directory.

```lua
local dcsbot	= base.dcsbot
local myplugin  = myplugin or {} 

--[[
If you want to dynamically load some lua into your mission, you do this in your onMissionLoadEnd hook.
Best is to load a file name mission.lua, to have some kind of naming standard, but you can name it
as you like.
The base commands of DCSServerBot are loaded into the mission environment by the bot already, so you have
some commands available that you can use (see mission.lua). 
]]
function myplugin.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'MyPlugin: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/myplugin/mission.lua' .. '\\")")')
end

function myplugin.onPlayerConnect(id)
    local msg = {}
    msg.command = 'myCustomCommand'
    msg.id = id
    dcsbot.sendBotTable(msg)
end

DCS.setUserCallbacks(myplugin)
```

### commands.lua

If you want to send a command by the bot into the DCS Hooks environment, you implement the command in here.
So if you for instance run .pause in Discord, this will result in a JSON message with 
"command": "pauseMission" to DCS and call a function pauseMission() that is implemented in some commands.lua 
in one of the bots plugins.

```lua
local base = _G
local dcsbot = base.dcsbot

function dcsbot.pauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: pauseMission()') 
    DCS.setPause(true)
end
```

### mission.lua

This is a special file and does not necessarily need to be named like this. But I would still recommend
it, as you then know which of the lua files will be loaded into the mission environment (if you do so
in your onMissionLoadEnd (see above)).
The following commands can be used in the mission environment (ME):

```lua
function sendBotMessage(msg, channel) end
function sendBotTable(tbl, channel) end
function sendEmbed(title, description, img, fields, footer, channel) end
function updateEmbed(id, title, description, img, fields, footer, channel) end
function callback(msg, channel) end
function startMission(id) end
function restartMission() end
function disableUserStats() end
```

## Data Classes

To ease the access to server, player and mission information and to run usual commands, DCSServerBot
provides classes to do so.

### Server

See `server.py`. You have usually two options to retrieve the running server instance:

a) Plugin

In your plugins, you usually want to run a Discord command that sends information to a specific server.
To get a server or player instance, you can use the existing transformers of the core framework:

```python
import discord

from core import utils, Server, Status, Player
from discord import app_commands    

@app_commands.command(description='This is a sample command.')
@app_commands.guild_only()
@utils.app_has_role('DCS')
async def sample(self, interaction: discord.Interaction,
                 server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                 player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)]):
    player.sendPopupMessage("Hello World!")
```

b) EventListener

In your EventListener, you receive commands from a DCS server, so you get the server already in your method calls:

```python
@event(name="onChatMessage")
async def onChatMessage(self, server: Server, data: dict) -> None:
    ...
```

### Mission

The running mission (`mission.py`) can be accessed through the Server instance like so:

```python
mission: Mission = server.current_mission
```

### Player

There are several ways to access a player (`player.py`):
* by their UCID
* by their Discord ID (if they are a Discord member and properly mapped)
* by their in-game ID (1, 2, 3, ...)
* by their in-game name (which is fortunately unique per session)

This can be achieved by asking your server about the player and providing the relevant parameter to the
`get_player()` method:

```python
@event()
async def onChatMessage(self, server: Server, data: dict) -> None:
    player: Player = server.get_player(id=data['from_id'])
```

### discord.Member

As DCSServerBot stores a link between DCS players and Discord members, you surely can access the discord
member information, too:

```python
@event()
async def onChatCommand(self, server: Server, data: dict) -> None:
    player: Player = server.get_player(id=data['from_id'])
    if player.member:
        self.log.info(f"Player {player.name} is member {player.member.display_name}!")
```

## Reports

See [Report Framework].

## Versioning

Every plugin has its own version. Versioning starts with a file named version.py like so:

_version.py:_
```python
__version__ = "1.0"
```
You only want to change the plugins version, if a change to the underlying database has taken place or if 
some other migration is needed. You _can_ express major changes by version number changes, too, but this is not
a must.

## Database Handling

DCSServerBot uses a PostgreSQL database to hold all tables, stored procedures and whatnot. Every plugin can
create its own database elements. To do so, you need to add the DDL, line by line in a file named tables.sql 
below the optional "db" directory.

_tables.sql:_
```sql
CREATE TABLE IF NOT EXISTS bans (ucid TEXT PRIMARY KEY, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT NOW());
```

To access the database, you should use the database pool that is available in every common framework class:

```python
with self.pool.connection() as conn:
    with conn.transaction():
        conn.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                     (player.ucid, self.plugin_name, reason))
```

## Auto-Migration

DCSServerBot was invented to ease the life of DCS server admins. That said, you should take care of your 
fellow admins and create code that migrates database tables / entries or any config file that needs to be
amended automatically. There are lots of little helpers in the DCSServerBot framework to do so.

Whenever a version of a plugin changes (version.py), DCSServerBot runs several update mechanisms, that you can implement 
if necessary:

### Database Table Migration

Just implement a script named "update_vX.Y.sql", where X.Y is there version where you want to migrate **FROM**.
To migrate the database from plugin version 1.0 to 1.1, you need to implement a script named update_v1.0.sql.

_update_v1.0.sql:_
```sql
ALTER TABLE bans ADD COLUMN (test TEXT NOT NULL DEFAULT 'n/a');
```

### Any Other Migration

Each Plugin can implement the method 
```python
    def migrate(self, version: str) -> None:
        pass
```
that will take care of anything that needs to be done when migrating **TO** version _version_. 

[Punishment]: punishment.md
[discord.py]: https://discordpy.readthedocs.io/en/stable/
[Report Framework]: ../reports/index.md
