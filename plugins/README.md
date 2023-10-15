# Plugin System
DCSServerBot is a modular system. It already provides a rich platform and many useful tools and utilities, 
but you can always extend the platform by writing your own custom plugin. The bot will take over the 
burden of making the different commands and codes available in DCS or Discord, but you still need to code 
a bit.

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
Each plugin _can_ use a yaml file to keep its config parameters. There yaml files are stored in ./config/plugins,
and it is a good habit to provide a sample for it.
As each plugin might need a different configuration for each server and maybe some default configuration,
the layout of the config files is as follows:
```YAML
DEFAULT:
  name: I am the default section
DCS.openbeta_server:
  name: I am a server specific section
```
To access the configuration, you can use the following patterns in your plugin implementation:
```python
    # Default section
    config: dict = self.get_config()
    # Server specific section
    config: dict = self.get_config(server)
    # Configuration of another plugin (2 ways)
    config: dict = self.get_config(server, plugin_name="Admin")
    config: dict = interaction.client.cogs['Admin'].get_config(server)
```
To access the configuration in your EventListener, you need to prepend self.plugin: 
```python
    config: dict = self.plugin.get_config(server)
    # ...
```
> ⚠️ **Attention!**<br/>
> If you access the server specific configuration, the default configuration will be merged with the respective server 
> specific configuration, giving the server specific configuration the priority over the default. If you don't want it 
> like that, you need to overwrite the get_config() method in your own plugin implementation 
> (ex: [greenieboard](./greenieboard/commands.py)).


## Classes
When implementing a plugin, there are some python classes that you need to know.

### Class: Plugin
Base class for all plugins. Needs to be implemented inside the commands.py file (see below).<br/>
You have access to the following class variables:
* self.plugin_name: Plugin name ("sample")
* self.plugin_version: Plugin version ("1.0")
* self.bot: the global DCSServerBot instance
* self.log: Logging
* self.pool: Database pool
* self.loop: asyncio event loop
* self.locals: dict from your plugin.yaml
* self.eventlistener: the EventListener instance bound to this plugin (optional)

```python
import psycopg

from core import Plugin, TEventListener
from services import DCSServerBot
from typing import Type


class Sample(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        # do something when the plugin is initialized
    
    async def cog_load(self) -> None:
        await super().cog_load()
        # do something async when the plugin is (re-)loaded
        
    async def cog_unload(self) -> None:
        await super().cog_unload()
        # do something when the plugin is unloaded
        
    async def on_ready(self) -> None:
        await super().on_ready()
        # do something when the bot starts listening

    async def install(self) -> None:
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

    async def prune(self, conn: psycopg.Connection, *, days: int = -1, ucids: list[str] = None) -> None:
        # cleanup (the database) with data older than days and/or for specific users (ucids) 
        pass

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str) -> None:
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
* self.prefix: the in-game chat command prefix 

```python
from core import EventListener, Server, Plugin, Player, event, chat_command


class SampleEventListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        # do something when the listener is initialized
        
    async def shutdown(self) -> None:
        await super().shutdown()
        # do something when the plugin/listener is stopped
        
    # register a callback event (name is optional, the function name will be used as default)
    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # called, when a DCS server is found and initialized
        # dict contains a dictionary with a lot of server information, like name, mission, active players,
        # weather and whatnot.
        pass
    
    # the following callbacks are derived from the Hooks environment:
    @event(name="onMissionLoadBegin")
    async def onMissionLoadBegin(self, server: Server, data: dict) -> None:
        pass

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onSimulationPause")
    async def onSimulationPause(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        pass
    
    @event(name="onChatMessage")
    async def onChatMessage(self, server: Server, data: dict) -> None:
        pass

    # Register an in-game chat command, that can be called by typing in the in-game chat.
    # The command will automatically register in the in-game help command. You can specify optional roles that can
    # fire the command.
    @chat_command(name="sample", aliases=["simple"], roles=['DCS Admin', 'GameMaster'], help="a sample command")
    async def sample(self, server: Server, player: Player, params: list[str]):
        pass
```

## Main Files

### commands.py
This is the entry point for any Discord command. For how to handle Discord commands, see [discord.py](https://discordpy.readthedocs.io/en/stable/).
```python
import discord

from core import command, Plugin, utils, Server
from discord import app_commands
from services import DCSServerBot

from .listener import SampleEventListener


class Sample(Plugin):
    
    # This command should only run on servers that are in the state RUNNING, PAUSED or STOPPED.
    @command(description='This is a sample command.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sample(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer], text: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        # do something that takes some time
        await interaction.followup.send(f"I did something on server {server.name} using text {text}.")
        
        
async def setup(bot: DCSServerBot):
    await bot.add_cog(Sample(bot, SampleEventListener))
```

### listener.py
This is just the implementation of the EventListener class (see above). An EventListener is optional, you only need
it, if you want to listen to DCS events, or if you want to provide in-game chat events.

### lua/callbacks.lua
Every plugin can have their own DCS World hook, that will be automatically added to the Scripts\Hooks
environment. To achieve this, you just need to place a file named callbacks.lua in your lua directory.
The naming scope is always unique for your callbacks, usually the plugin name.
```lua
local dcsbot	= base.dcsbot

local myplugin = myplugin or {} 

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

### lua/commands.lua
If you want to send a command by the bot into the DCS Hooks environment, you implement the command in here.
So if you for instance run .pause in Discord, this will result in a JSON message with 
"command": "pauseMission" to DCS and call a function pauseMission() that is implemented in some commands.lua 
in one of the bots plugins. The naming scope is always "dcsbot" for commands.
```lua
local base = _G
local dcsbot = base.dcsbot

function dcsbot.pauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: pauseMission()') 
    DCS.setPause(true)
end
```

### lua/mission.lua
This is a special file and does not necessarily need to be named like this. But I would still recommend
it, as you then know which of the lua files will be loaded into the mission environment (if you do so
in your onMissionLoadEnd (see above)).
The following DCSServerBot functions can be used in the mission environment (ME):
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

## DCSServerBot Data Classes
To ease the access to server, player and mission information and to run usual commands, DCSServerBot
provides classes to do so. As the bot can be run over multiple locations, it might happen, that the master node
needs to talk to any of the other nodes. In this case, many of the internal objects have so called Proxy-classes, that
handle the necessary remote procedure calls. For you as a user, this will be transparent, as long as you don't decide
to implement your own dataclass. Then you need to tackle the situation, that you might not be on the same PC as your
dataclass is at the moment. We will go more into deep later in this guide.

### Server
A [server](../core/data/server.py) object is needed to work with anything related to the DCS server. You can retrieve this object on two
ways, depending on whether you are in a Plugin- or in an EventListener-context.

a) Plugin<p>
In your plugins, you usually want to run a Discord command that sends information to a specific server.
To get the respective server, you can take advantage of the channel/server mapping that DCSServerBot 
implements in its configuration already. That means, that if you for instance run a command in the
dedicated admin channel of any server, you can access the Server instance, directly through the Discord
context. If you have a central admin channel, you automatically get a selection of the server to run the command
onto. And if you only have one server, you always get that single server already. To do so, you just need to
use the ServerTransformer in your command declaration. You can even specify, if you only want to get servers in a 
specific state.<br>
> ⚠️ **Attention!**<br>
> The state of a server will only be taken into consideration, if you use the server selection.
```python
    @command(description='This is a simple pause command.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def shutdown(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING]))]):
        await server.pause()
        await interaction.response.send_message(f"Server {server.name} has been paused.")
```

b) EventListener<p>
In your EventListener, you receive the server the event came from already in the event call itself:
```python
    @event(name="mySampleEvent")
    async def mySampleEvent(self, server: Server, data: dict) -> None:
        await server.restart()
```

### Instance
The [instance](../core/data/instance.py) object represents a DCS instance. Usually, every server has its own instance and every
instance has a server assigned. They can switch though, which is why they are decoupled into these both objects.<br>
In general, you only need the instances name, to access information in config files or the like.
```python
    instance: Instance = server.instance
    await interaction.response.send_message(f"Server {server.name} runs on instance {instance.name}.")
```

### Mission
The running [mission](../core/data/mission.py) can be accessed through the Server object like so:
```python
    mission: Mission = server.current_mission
    await interaction.response.send_message(f"Server {server.name} is running {mission.name}.")
```

### Player
There are several ways to access a [player](../core/data/player.py):
* by their UCID
* by their Discord ID (if they are a Discord member and properly linked)
* by their in-game ID (1, 2, 3, ...)
* by their in-game name (which is fortunately unique per session)

This can be achieved by asking your server about the player and providing the relevant parameter to the
get_player() method:
```python
    player: Player = server.get_player(discord_id=interaction.user.id)
    if player:
        await interaction.response.send_message(f"You are currently logged on as user {player.name}!")
    else:
        await interaction.response.send_message(f"You are currently not logged into the DCS server or your account 
                                                is not properly linked.")
```

### discord.Member
As DCSServerBot stores a link between DCS players and Discord members, you can access the member information also:
```python
    @chat_command(name="linkcheck", help="check if you are linked")
    async def linkcheck(self, server: Server, player: Player, params: list[str]):
        if player.member:
            player.sendChatMessage(f"You are linked to member {player.member.display_name}.")
        else:
            player.sendChatMessage(f"You are not linked.")
```

## Reports
See [Report Framework](../reports/README.md).

## Versioning
Every plugin has its own version. Versioning starts with a file named version.py like so:</br>

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
below the optional "db" directory.<br/>

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

Sample `update_v1.0.sql`:
```sql
ALTER TABLE bans ADD COLUMN (test TEXT NOT NULL DEFAULT 'n/a');
```

### Any Other Migration
Each Plugin can implement the method 
```python
    def migrate(self, version: str) -> None:
        if version == '1.1':
            # change the config.yaml file to represent the changes introduced in version 1.1
            pass
```
that will take care of anything that needs to be done when migrating **TO** version `version`. 
