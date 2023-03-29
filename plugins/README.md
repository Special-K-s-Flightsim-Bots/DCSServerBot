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
      "installation": "DCS.openbeta_server",
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
in your EventListener implementation.<p>
**__Attention:__**<br/>
The default configuration will be merged with the respective server specific configuration, giving the
server specific configuration the priority over the default. If you don't want it like that, you need to
overwrite the get_config() method in your own plugin configuration (see [punishment](./punishment/commands.py)).


## Classes
When implementing a plugin, there are some python classes that you need to know.

### Class: Plugin
Base class for all plugins. Needs to be implemented inside the commands.py file (see below).<br/>
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
    
    async def cog_unload(self):
        super.cog_unload()
        # do something when the plugin is unloaded

    def install(self):
        super.install()
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
* self.commands: a list of commands implemented by this listener (autofilled) 

```python
from core import EventListener, Server, Plugin, Player


class SampleEventListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        # do something when the listener is initialized
        
    async def registerDCSServer(self, data: dict) -> None:
        # called, when a DCS server is found and initialized
        # dict contains a dictionary with a lot of server information, like name, mission, active players,
        # weather and whatnot.
    
    # the following callbacks are derived from the Hooks environment:
    async def onMissionLoadBegin(self, data: dict) -> None:
        pass

    async def onMissionLoadEnd(self, data: dict) -> None:
        pass
    
    async def onSimulationStart(self, data: dict) -> None:
        pass
    
    async def onSimulationStop(self, data: dict) -> None:
        pass
    
    async def onSimulationPause(self, data: dict) -> None:
        pass
    
    async def onSimulationResume(self, data: dict) -> None:
        pass
    
    async def onPlayerConnect(self, data: dict) -> None:
        pass
    
    async def onPlayerStart(self, data: dict) -> None:
        pass
    
    async def onPlayerStop(self, data: dict) -> None:
        pass
    
    async def onPlayerChangeSlot(self, data: dict) -> None:
        pass
    
    async def onGameEvent(self, data: dict) -> None:
        pass
    
    async def onChatMessage(self, data: dict) -> None:
        pass

    # onChatCommand is called, whenever a command is fired in the ingame-chat
    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'])
        if data['subcommand'] == 'atis':
            # can be used by everyone ...
            pass
        elif data['subcommand'] == 'restart' and player.has_discord_roles(['DCS Admin']):
            # can only be used by the DCS Admin role ...
            pass

    async def myCustomCommand(self, data: dict) -> None:
        # any custom command, which can be sent from the lua environment by using
        # local msg = {}
        # msg.command = 'myCustomCommand'
        # msg.param = { ... } -- all that goes into the dict
        # dcsbot.sendBotTable(msg)
```

## Main Files

### commands.py
This is the entry point for any Discord command. As DCSServerBot can be installed in multiple locations 
serving a bunch of DCS servers, you might end up with more than one active bot in your discord. To separate 
them, commands are usually called in the admin channels of each server (see examples below).
Sometimes commands needs to query the whole database or must only be run on exactly one instance, the 
so-called MASTER instance. For this case, you already need to take care of the MASTER / AGENT split in your
Discord hook. For how to handle Discord commands, see [discord.py](https://discordpy.readthedocs.io/en/stable/).
```python
import platform
from core import Plugin, utils, Server, DCSServerBot
from discord.ext import commands
from .listener import SampleEventListener


class SampleAgent(Plugin):
    @commands.command(description='Agent-command', usage='<text>')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def sample(self, ctx, text: str):
        server: Server = await self.bot.get_server(ctx)
        if server:
            await ctx.send(f'Server {server.name} is {server.status.name}!')
        else:
            # just for documentation purposes, Agents, only implement server-specific commands!
            pass

        
class SampleMaster(SampleAgent):
    @commands.command(description='Master-only command')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def master(self, ctx):
        await ctx.send(f'This command is running on node {platform.node()}')


async def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(SampleMaster(bot, SampleEventListener))
    else:
        await bot.add_cog(SampleAgent(bot, SampleEventListener))
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
See [server.py](../core/data/server.py).<br/>
You have usually two options to retrieve the running server instance:

a) Plugin<br/>
In your plugins, you usually want to run a Discord command that sends information to a specific server.
To get the respective server, you can take advantage of the channel/server mapping that DCSServerBot 
implements in its configuration already. That means, that if you for instance run a command in the
dedicated admin channel of any server, you can access the Server instance, directly through the Discord
context:
```python
async def sample(self, ctx, text: str):
    server: Server = await self.bot.get_server(ctx)
```

b) EventListener<br/>
In your EventListener, you receive commands from a DCS server. And sure - that server tells you its name
with every command it sends to you:
```python
async def onChatCommand(self, data: dict) -> None:
    server: Server = self.bot.servers[data['server_name']]
```

### Mission
The running [mission](../core/data/mission.py) can be accessed through the Server instance like so:
```python
mission: Mission = server.current_mission
```

### Player
There are several ways to access a [player](../core/data/player.py):
* by their UCID
* by their Discord ID (if they are a Discord member and properly mapped)
* by their in-game ID (1, 2, 3, ...)
* by their in-game name (which is fortunately unique per session)

This can be achieved by asking your server about the player and providing the relevant parameter to the
get_player() method:
```python
async def onChatCommand(self, data: dict) -> None:
    server: Server = self.bot.servers[data['server_name']]
    player: Player = server.get_player(id=data['from_id'])
```

### discord.Member
As DCSServerBot stores a link between DCS players and Discord members, you surely can access the discord
member information, too:
```python
async def onChatCommand(self, data: dict) -> None:
    server: Server = self.bot.servers[data['server_name']]
    player: Player = server.get_player(id=data['from_id'])
    if player.member:
        self.log.info(f"Player {player.name} is member {player.member.display_name}!")
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
conn = self.pool.getconn()
try:
    with closing(conn.cursor()) as cursor:
        cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                       (player.ucid, self.plugin_name, reason))
        conn.commit()
except (Exception, psycopg2.DatabaseError) as error:
    conn.rollback()
    self.log.exception(error)
finally:
    self.pool.putconn(conn)
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
