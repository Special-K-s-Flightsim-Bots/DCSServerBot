# Plugin System
DCSServerBot is a modular system. It already provides a rich platform and many useful tools and utilities, 
but you can always extend the platform by writing your own custom plugin. The bot will take over the 
burden of making the different commands and codes available in DCS or Discord, but you still need to program 
a bit on your own.

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
Each plugin _can_ use a YAML file to keep its config parameters. 
The YAML files are stored in ./config/plugins, and it is a good habit to provide a sample for it.
As each plugin might need a different configuration for each server and maybe some default configuration,
the layout of the config files is as follows:
```YAML
DEFAULT:
  name: I am the default section
DCS.release_server:
  name: I am the instance-specific section (aka server specific)
```
To access the configuration, you can use the following pattern in your plugin implementation:
```python
import discord
from core import Plugin, Server


class MyPlugin(Plugin):
    def my_function(self, interaction: discord.Interaction, server: Server):
        # Default section
        config: dict = self.get_config()
        # Server-specific section
        config: dict = self.get_config(server)
        # Configuration of another plugin (2 ways)
        config: dict = self.get_config(server, plugin_name="Admin")
        config: dict = interaction.client.cogs['Admin'].get_config(server)
```
To access the configuration in your EventListener, you need to prepend self.plugin: 
```python
from core import EventListener, Server


class MyEventListener(EventListener):

    async def my_function(self, server: Server):
        config: dict = self.plugin.get_config(server)
        # ...
```
> [!NOTE]
> If you access the server-specific configuration, the default configuration will be merged with the respective 
> server-specific configuration, giving the server-specific configuration priority over the default. 
> If you don't want it like that, you need to overwrite the `get_config()` method in your own plugin implementation 
> (ex: [greenieboard](./greenieboard/commands.py)).

## Classes
When implementing a plugin, there are some Python classes that you need to know:

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
from services.bot import DCSServerBot
from typing import Type, Optional

from .listener import SampleEventListener


class Sample(Plugin[SampleEventListener]):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        # do something when the plugin is initialized
        ...
    
    async def cog_load(self) -> None:
        await super().cog_load()
        # do something async when the plugin is (re-)loaded
        ...
        
    async def cog_unload(self) -> None:
        # do something when the plugin is unloaded
        ...
        await super().cog_unload()
        
    async def on_ready(self) -> None:
        await super().on_ready()
        # do something when the bot starts listening
        ...

    async def install(self) -> None:
        await super().install()
        # do something when the plugin is installed for the first (!) time
        ...

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        # do something when the plugin is migrated (see below)
        ...
    
    async def before_dcs_update(self) -> None:
        # do something before a DCS upgrade takes place
        ...

    async def after_dcs_update(self) -> None:
        # do something after a DCS upgrade took place and before the servers are started
        ...

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        # cleanup (the database) with data older than days and/or for specific users (ucids) 
        ...

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str) -> None:
        # this function has to be implemented in your own plugins if a server rename takes place
        ...

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        # this function has to be implemented in your own plugins if the ucid of a user changed (steam <=> standalone)
        ...
```
> [!NOTE]
> None of these methods needs to be overloaded for a plugin to work.

### Class: EventListener
You have access to the following class variables:
* self.plugin: the Plugin implementation bound to this EventListener
* self.plugin_name: name of the plugin
* self.bot: the Discord bot
* self.log: a standard logger
* self.apool: an asynchronous Database pool (preferred)
* self.pool: a synchronous Database pool (only use this if there is no other option)
* self.loop: asyncio event loop
* self.locals: the configuration (<plugin_name>.yaml) as a dict
* self.prefix: the in-game chat command prefix (EventListener only)

```python
from core import EventListener, Server, Plugin, Player, event, chat_command
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Sample


class SampleEventListener(EventListener["Sample"]):
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
        ...
    
    # the following callbacks are derived from the Hooks environment:
    @event(name="onMissionLoadBegin")
    async def onMissionLoadBegin(self, server: Server, data: dict) -> None:
        ...

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onSimulationPause")
    async def onSimulationPause(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        ...
    
    @event(name="onChatMessage")
    async def onChatMessage(self, server: Server, data: dict) -> None:
        ...

    # Register an in-game chat command that can be called by typing in the in-game chat.
    # The command will automatically register in the in-game help command. You can specify optional roles that can
    # fire the command.
    @chat_command(name="sample", aliases=["simple"], roles=['DCS Admin', 'GameMaster'], help="a sample command")
    async def sample(self, server: Server, player: Player, params: list[str]):
        ...
```

## Main Files

### commands.py
This serves as the starting point for all Discord commands. 
To learn about handling Discord commands, please refer to the documentation at [discord.py](https://discordpy.readthedocs.io/en/stable/).
```python
import discord

from core import command, Plugin, utils, Server, Status
from discord import app_commands
from services.bot import DCSServerBot

from .listener import SampleEventListener


class Sample(Plugin[SampleEventListener]):
    
    # This command should only run on servers that are in the state RUNNING, PAUSED or STOPPED.
    @command(description='This is a sample command.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sample(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                         Status.RUNNING, Status.PAUSED, Status.STOPPED
                     ])], text: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        # do something that takes some time
        ...
        await interaction.followup.send(f"I did something on server {server.name} using text {text}.")
        
        
async def setup(bot: DCSServerBot):
    await bot.add_cog(Sample(bot, SampleEventListener))
```

### listener.py
This is the implementation of the EventListener class (see above). 
An EventListener is optional, you only need it if you want to listen to DCS events or if you want to provide in-game 
chat events.

### lua/callbacks.lua
Every plugin can have their own DCS World hook that will be automatically added to the Scripts\Hooks environment. 
To achieve this, you need to place a file named `callbacks.lua` in your lua directory.
The naming convention for your callbacks should always be unique, typically based on the name of the plugin.
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

Sim.setUserCallbacks(myplugin)
```

### lua/commands.lua
To dispatch a command from the bot into the DCS Hooks environment, you should define the command here. 
For example, when you type /server pause in Discord, it generates a JSON message to DCS as follows:
```json
{
  "command": "pauseMission"
}
```
This then invokes the function pauseMission(), which is implemented in the commands.lua file within one of the bot's 
plugins. The naming space for commands is consistently set as "dcsbot".

```lua
local base = _G
local dcsbot = base.dcsbot

function dcsbot.pauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: pauseMission()') 
    Sim.setPause(true)
end
```

### lua/mission.lua
This file serves a particular purpose and is not strictly required to have this name. 
Nevertheless, it's suggested for easy identification as it will be loaded into the mission environment 
(if you set it up through onMissionLoadEnd (refer above)).

These DCSServerBot functions can be used within the mission scripting environment (MSE):
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

> [!TIP]
> To make sure that the lua code inside your missions will run with and without DCSServerBot being installed on the 
> respective DCS server, I recommend checking the existence of DCSServerBot like so:
> ```lua
> if dcsbot then
>   ... -- add code that needs DCSSB being installed
> end
> ```

## DCSServerBot Data Classes
To facilitate access to server, player, and mission data, as well as executing standard commands, DCSServerBot offers 
classes for those purposes. 
Given that the bot may run across various locations, it's possible that the master node needs to communicate with any 
of the other nodes. 
To manage such internode communication, several internal objects come equipped with what are known as Proxy-classes, 
which handle remote procedure calls. 
As a user, you won't generally encounter this complexity unless you opt to create your own dataclass. 
In that case, you would need to address the situation where your dataclass is not currently located on the same 
computer as you are. 
This topic will be elaborated upon later in this guide.

### Server
A [server](../core/data/server.py) object is needed to work with anything related to the DCS server. 
You can retrieve this object in two ways, depending on whether you are in a Plugin- or in an EventListener-context.

a) Plugin<p>
Within your plugins, you often desire to trigger a Discord command that sends data to a specific server. 
By using the channel/server mapping established by DCSServerBot in its configuration, you can get the 
corresponding Server instance through the Discord context. For example, if you run a command in a dedicated admin 
channel for any given server, you can directly access the Server instance via the Discord context. 
If you have a central admin channel, you will automatically be presented with a list of servers to execute the 
command on. In the case where you only have one server, that single server will always be available. 
To achieve this, it's essential to employ the ServerTransformer in your command declaration and even define 
if you wish to focus on servers in a specific state.

> [!NOTE]
> The state of a server will only be taken into consideration if you use the server selection.
```python
import discord

from core import command, Plugin, utils, Server, Status
from discord import app_commands
from services.bot import DCSServerBot

from .listener import SampleEventListener


class Sample(Plugin[SampleEventListener]):
    @command(description='This is a simple pause command.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def pause(self, interaction: discord.Interaction, 
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        await server.current_mission.pause()
        await interaction.response.send_message(f"Server {server.name} has been paused.")
```

b) EventListener<p>
In your EventListener, you already have access to the server the event originated from within the event call itself:
```python
from core import EventListener, Server, Player, event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Sample


class SampleEventListener(EventListener["Sample"]):

    @event(name="mySampleEvent")
    async def mySampleEvent(self, server: Server, data: dict) -> None:
        await server.restart()
```

### Instance
The [instance](../core/data/instance.py) object represents a DCS instance.
Typically, each server has its own instance, and every instance is assigned a server. 
Although they can switch, their separation into distinct objects is necessary due to this flexibility. 
In most cases, you only require the instance name to retrieve data from configuration files or similar resources.
```python
import discord
from core import Instance, Server

async def xxx(interaction: discord.Interaction, server: Server):
    instance: Instance = server.instance
    await interaction.response.send_message(f"Server {server.name} runs on instance {instance.name}.")
```

### Mission
The running [mission](../core/data/mission.py) can be accessed through the Server object like so:
```python
import discord
from core import Mission, Server

async def xxx(interaction: discord.Interaction, server: Server):
    mission: Mission = server.current_mission
    await interaction.response.send_message(f"Server {server.name} is running {mission.name}.")
```

### Player
There are several ways to access a [player](../core/data/player.py):
* by their UCID
* by their Discord ID (if they are a Discord member and properly linked)
* by their in-game ID (1, 2, 3, ...)
* by their in-game name (which is unique per session)

This can be achieved by asking your server about the player and providing the relevant parameter to the
`get_player()` method:
```python
import discord
from core import Server, Player

async def xxx(interaction: discord.Interaction, server: Server):
    player: Player = server.get_player(discord_id=interaction.user.id)
    if player:
        await interaction.response.send_message(f"You are currently logged on as user {player.name}!")
    else:
        await interaction.response.send_message(f"You are currently not logged into the DCS server or your account " 
                                                "is not properly linked.")
```

### discord.Member
Since DCSServerBot maintains a connection between DCS players and Discord members, you are able to retrieve member 
information as well.
```python
from core import EventListener, Server, Player, chat_command
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Sample

    
class SampleEventListener(EventListener["Sample"]):
    @chat_command(name="linkcheck", help="check if you are linked")
    async def linkcheck(self, server: Server, player: Player, params: list[str]):
        if player.member:
            await player.sendChatMessage(f"You are linked to member {player.member.display_name}.")
        else:
            await player.sendChatMessage(f"You are not linked.")
```

## Reports
See [Report Framework](../reports/README.md).

## Versioning
Each plugin includes its own version. Versioning begins with a file named `version.py` as follows:

version.py:
```py
__version__ = "1.0"
```
> [!NOTE]
> You should only modify the plugin version when there is a change to the underlying database or if some other 
> migration is required. 
> It's possible to denote significant changes through version number alterations, but it's not mandatory.

## Database Handling
DCSServerBot employs a PostgreSQL database to store all tables, stored procedures, and other data structures. 
Each plugin can create its own database elements. 
To achieve this, you need to add DDL (Data Definition Language) instructions in a file named `tables.sql` within the 
optional "db" directory below your plugin directory.

tables.sql:
```sql
CREATE TABLE IF NOT EXISTS bans (
    ucid TEXT PRIMARY KEY, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

To interact with the database, it is recommended to use the asynchronous database pool offered by each common 
framework class:
```python
from core import Plugin, Player

class MyPlugin(Plugin):

    async def ban_player(self, player: Player, reason: str = 'n/s'):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO bans (ucid, banned_by, reason) 
                    VALUES (%s, %s, %s) 
                    ON CONFLICT DO NOTHING
                """, (player.ucid, self.plugin_name, reason))
```

## Third-party Python libraries
If your solution needs additional third-party libraries, you can define them in a file named `requirements.local` at 
the root level of your DCSServerBot installation. 
This file is not present by default as it's unnecessary unless required. 
It must be created and populated with library dependencies only when they are necessary.

Example:
```requirements
# wxPython: GUI library to build Windows-like UI components
wxpython==4.2.3
```

To install these libraries, you can use the following command within a "cmd.exe" terminal in your bot's installation 
folder:
`%USERPROFILE%\.dcssb\Scripts\pip install -r requirements.local`

## Auto-Migration
DCSServerBot was designed to streamline the workload of server administrators. However, it's crucial to also 
consider your fellow administrators and develop code that can automate the migration of database tables, entries, or 
any configuration files that require adjustments. Fortunately, the DCSServerBot framework offers many utilities to 
facilitate such tasks.

Whenever a version of a plugin changes (version.py), DCSServerBot runs several update mechanisms that you can implement 
if necessary:

### Database Table Migration
Implement a script named `db\update_vX.Y.sql`, where X.Y is there version you want to migrate **FROM**.
To migrate the database from plugin version 1.0 to 1.1, you need to implement a script named update_v1.0.sql.

Sample `db\update_v1.0.sql`:
```sql
ALTER TABLE bans ADD COLUMN test TEXT NOT NULL DEFAULT 'n/a';
```

### Any Other Migration
Each plugin can define the `migrate()` method as follows: 
```python
import psycopg

from core import Plugin
from typing import Optional

from .listener import SampleEventListener


class Sample(Plugin[SampleEventListener]):

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == '1.1':
            # change the config.yaml file to represent the changes introduced in version 1.1
            ...
            # don't forget to re-read the plugin configuration if you have changed any of it during migration.
            self.read_locals()
```
This function handles the tasks necessary for a migration to version `new_version`. 
